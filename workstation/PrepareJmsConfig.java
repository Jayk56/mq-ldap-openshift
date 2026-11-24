import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.channels.Channels;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.FileAttribute;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.Properties;
import java.util.Set;

class PrepareJmsConfig {
    public static void main(String[] args) {
        if (args.length != 3) {
            System.err.println("Usage: java PrepareJmsConfig.java SITE_CONFIG PASSWORD_FILE NEW_CONFIG");
            System.exit(2);
        }
        try {
            write(Path.of(args[0]), Path.of(args[1]), Path.of(args[2]));
        } catch (FileAlreadyExistsException e) {
            System.err.println("A file already exists at " + e.getFile() + ". Choose a new config filename.");
            System.exit(1);
        } catch (IOException | IllegalArgumentException e) {
            System.err.println("Could not prepare the workstation config. " + e.getMessage());
            System.exit(1);
        }
    }

    static void write(Path template, Path passwordFile, Path output) throws IOException {
        Properties config = new Properties();
        try (InputStream in = Files.newInputStream(template)) {
            config.load(in);
        }
        String password = Files.readString(passwordFile).replaceFirst("[\\r\\n]+\\z", "");
        if (password.isEmpty() || password.indexOf('\r') >= 0 || password.indexOf('\n') >= 0) {
            throw new IllegalArgumentException("The publisher password must be a single nonempty line.");
        }
        config.setProperty("PROVIDER_PASSWORD", password);

        if (Files.exists(output, LinkOption.NOFOLLOW_LINKS)) {
            throw new FileAlreadyExistsException(output.toString());
        }
        Path directory = output.toAbsolutePath().getParent();
        Path partial = output.resolveSibling(output.getFileName() + ".partial");
        FileAttribute<?>[] permissions = {};
        if (Files.getFileStore(directory).supportsFileAttributeView("posix")) {
            var ownerOnly = PosixFilePermissions.fromString("rw-------");
            permissions = new FileAttribute<?>[]{PosixFilePermissions.asFileAttribute(ownerOnly)};
        }
        var options = Set.of(StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE);
        try (OutputStream out = Channels.newOutputStream(Files.newByteChannel(partial, options, permissions))) {
            config.store(out, null);
        }
        Files.move(partial, output);
    }
}
