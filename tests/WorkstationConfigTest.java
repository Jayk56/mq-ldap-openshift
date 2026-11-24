import java.io.InputStream;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.Arrays;
import java.util.Properties;

class WorkstationConfigTest {
    public static void main(String[] args) throws Exception {
        Path dir = Path.of(args[0]);
        Path template = Path.of("workstation", "JMS30Admin.config.example");
        Path passwordFile = dir.resolve("test-password");
        Path output = dir.resolve("test.config");
        String password = " leading \\q=:\u00e9\ud83d\udd11 trailing ";
        Files.writeString(passwordFile, password + "\r\n");
        PrepareJmsConfig.write(template, passwordFile, output);

        Properties loaded = new Properties();
        try (InputStream in = Files.newInputStream(output)) {
            loaded.load(in);
        }
        require(password.equals(loaded.getProperty("PROVIDER_PASSWORD")), "Password changed in transit");
        require("simple".equals(loaded.getProperty("SECURITY_AUTHENTICATION")), "Lost a site setting");
        if (Files.getFileStore(dir).supportsFileAttributeView("posix")) {
            require(Files.getPosixFilePermissions(output).equals(PosixFilePermissions.fromString("rw-------")),
                    "Expected an owner-only config");
        }

        byte[] original = Files.readAllBytes(output);
        Files.writeString(passwordFile, "replacement");
        try {
            PrepareJmsConfig.write(template, passwordFile, output);
            throw new AssertionError("Replaced an existing config");
        } catch (FileAlreadyExistsException expected) {
            require(Arrays.equals(original, Files.readAllBytes(output)), "Existing config changed");
        }

        Path next = dir.resolve("next.config");
        Path partial = dir.resolve("next.config.partial");
        Files.writeString(partial, "unfinished");
        try {
            PrepareJmsConfig.write(template, passwordFile, next);
            throw new AssertionError("Reused an unfinished output");
        } catch (FileAlreadyExistsException expected) {
            require(Files.notExists(next) && Files.readString(partial).equals("unfinished"), "Partial file changed");
        }

        Path invalidOutput = dir.resolve("invalid.config");
        for (String invalid : new String[]{"", "\r\n", "one\ntwo", "one\rtwo"}) {
            Files.writeString(passwordFile, invalid);
            try {
                PrepareJmsConfig.write(template, passwordFile, invalidOutput);
                throw new AssertionError("Accepted an empty or multiline password");
            } catch (IllegalArgumentException expected) {
                require(Files.notExists(invalidOutput), "Created a config for an invalid password");
            }
        }
        System.out.println("Workstation config checks passed.");
    }

    private static void require(boolean value, String message) {
        if (!value) {
            throw new AssertionError(message);
        }
    }
}
