package example.mqldap;

import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Hashtable;
import javax.naming.ConfigurationException;
import javax.naming.Context;
import javax.naming.Name;
import javax.naming.RefAddr;
import javax.naming.Reference;
import javax.naming.directory.DirContext;
import javax.naming.directory.InitialDirContext;
import javax.naming.spi.ObjectFactory;

/** Open a new LDAP connection for each catalog lookup. The caller closes it. */
public final class LdapContextFactory implements ObjectFactory {
    @Override
    public Object getObjectInstance(Object object, Name name, Context parent,
                                    Hashtable<?, ?> environment) throws Exception {
        if (!(object instanceof Reference)) {
            return null;
        }
        Reference ref = (Reference) object;
        if (!DirContext.class.getName().equals(ref.getClassName())) {
            return null;
        }
        String url = setting(ref, "providerUrl");
        URI uri = URI.create(url);
        if (!"ldaps".equalsIgnoreCase(uri.getScheme()) || uri.getHost() == null) {
            throw new ConfigurationException("providerUrl requires an ldaps:// host");
        }
        String password = Files.readString(Path.of(setting(ref, "passwordFile")),
                StandardCharsets.UTF_8).replaceFirst("[\\r\\n]+\\z", "");
        if (password.isEmpty() || password.indexOf('\r') >= 0 || password.indexOf('\n') >= 0) {
            throw new ConfigurationException("The LDAP reader password must be a single nonempty line");
        }
        Hashtable<String, String> properties = new Hashtable<>();
        properties.put(Context.INITIAL_CONTEXT_FACTORY, "com.sun.jndi.ldap.LdapCtxFactory");
        properties.put(Context.PROVIDER_URL, url);
        properties.put(Context.SECURITY_AUTHENTICATION, "simple");
        properties.put(Context.SECURITY_PRINCIPAL, setting(ref, "bindDn"));
        properties.put(Context.SECURITY_CREDENTIALS, password);
        properties.put(Context.REFERRAL, "throw");
        properties.put("com.sun.jndi.ldap.connect.timeout", "5000");
        properties.put("com.sun.jndi.ldap.read.timeout", "5000");
        return new InitialDirContext(properties);
    }

    private static String setting(Reference ref, String key) throws ConfigurationException {
        RefAddr value = ref.get(key);
        if (value == null || !(value.getContent() instanceof String)
                || ((String) value.getContent()).isBlank()) {
            throw new ConfigurationException("Missing Tomcat resource attribute " + key);
        }
        return (String) value.getContent();
    }
}
