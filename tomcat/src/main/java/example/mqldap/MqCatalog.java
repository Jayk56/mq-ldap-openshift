package example.mqldap;

import javax.naming.CompositeName;
import javax.naming.ConfigurationException;
import javax.naming.Context;
import javax.naming.InitialContext;
import javax.naming.InvalidNameException;
import javax.naming.Name;
import javax.naming.NameNotFoundException;
import javax.naming.NamingException;
import javax.naming.Reference;
import javax.naming.directory.DirContext;
import javax.naming.ldap.Rdn;

/** Look up IBM MQ objects by their catalog alias. Use one open catalog for related lookups. */
public final class MqCatalog implements AutoCloseable {
    private static final String RESOURCE = "java:comp/env/ldap/MqCatalog";
    private final Context naming;
    private final DirContext directory;

    MqCatalog(Context naming, DirContext directory) {
        this.naming = naming;
        this.directory = directory;
    }

    public static MqCatalog open() throws NamingException {
        Context naming = new InitialContext();
        try {
            Object resource = naming.lookup(RESOURCE);
            if (!(resource instanceof DirContext)) {
                throw new ConfigurationException("Configure " + RESOURCE + " as a DirContext in Tomcat");
            }
            return new MqCatalog(naming, (DirContext) resource);
        } catch (NamingException | RuntimeException failure) {
            try {
                naming.close();
            } catch (NamingException closing) {
                failure.addSuppressed(closing);
            }
            throw failure;
        }
    }

    /** Supply the alias itself, such as OrdersIn, and the JMS interface the application expects. */
    public <T> T lookup(String alias, Class<T> type) throws NamingException {
        if (alias == null || alias.isBlank()) {
            throw new InvalidNameException("Supply a nonempty MQ catalog alias");
        }
        String base = directory.getNameInNamespace();
        if (base.isEmpty()) {
            throw new ConfigurationException("The LDAP URL points to the directory root. "
                    + "Include the catalog path, such as /ou=mq,dc=messaging.");
        }
        Name name = new CompositeName().add(new Rdn("cn", alias).toString());
        Object value = directory.lookup(name);
        if (value instanceof Context) {
            // LDAP can return an empty context for an absent name.
            ((Context) value).close();
            if (directory.getAttributes(name, new String[]{"cn"}).get("cn") == null) {
                throw new NameNotFoundException("No visible MQ catalog alias named " + alias + " under " + base);
            }
        } else if (value instanceof Reference) {
            String factory = ((Reference) value).getFactoryClassName();
            throw new ConfigurationException("Java could not create " + alias + " using " + factory
                    + ". Check the shared IBM client JARs and Java's JNDI object factory filters.");
        } else if (type.isInstance(value)) {
            return type.cast(value);
        }
        String actual = value == null ? "null" : value.getClass().getName();
        throw new NamingException("MQ catalog alias " + alias + " returned " + actual
                + ". The application requested " + type.getName() + ".");
    }

    @Override
    public void close() throws NamingException {
        try {
            directory.close();
        } finally {
            naming.close();
        }
    }
}
