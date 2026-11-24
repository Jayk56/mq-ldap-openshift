package example.mqldap;

import javax.naming.ConfigurationException;
import javax.naming.Name;
import javax.naming.NameNotFoundException;
import javax.naming.NamingException;
import javax.naming.Reference;
import javax.naming.directory.Attributes;
import javax.naming.directory.BasicAttributes;
import javax.naming.directory.InitialDirContext;
import javax.naming.ldap.Rdn;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class MqCatalogTest {
    private static class Directory extends InitialDirContext {
        Object result;
        Name lookupName;
        boolean closed;
        String base = "ou=mq,dc=messaging";

        Directory() throws NamingException { super(true); }
        @Override public Object lookup(Name name) { lookupName = name; return result; }
        @Override public Attributes getAttributes(Name name, String[] fields) { return new BasicAttributes(); }
        @Override public String getNameInNamespace() { return base; }
        @Override public void close() { closed = true; }
    }

    @Test void punctuationIsPartOfOneAliasAndBothContextsClose() throws NamingException {
        Directory naming = new Directory();
        Directory directory = new Directory();
        directory.result = "queue";
        String alias = "Billing, East/Returns";
        try (MqCatalog catalog = new MqCatalog(naming, directory)) {
            assertEquals("queue", catalog.lookup(alias, String.class));
            assertEquals(1, directory.lookupName.size());
            assertEquals(alias, new Rdn(directory.lookupName.get(0)).getValue());
        }
        assertTrue(naming.closed);
        assertTrue(directory.closed);
    }

    @Test void missingAliasClosesTheEmptyContextAndNamesTheAlias() throws NamingException {
        Directory directory = new Directory();
        Directory empty = new Directory();
        directory.result = empty;
        try (MqCatalog catalog = new MqCatalog(new Directory(), directory)) {
            NameNotFoundException error = assertThrows(NameNotFoundException.class,
                    () -> catalog.lookup("Missing", String.class));
            assertTrue(error.getMessage().contains("Missing"));
            assertTrue(empty.closed);
        }
    }

    @Test void unresolvedFactoryAndWrongObjectTypeGiveDifferentErrors() throws NamingException {
        Directory directory = new Directory();
        try (MqCatalog catalog = new MqCatalog(new Directory(), directory)) {
            directory.result = new Reference("example.Queue", "example.QueueFactory", null);
            ConfigurationException factory = assertThrows(ConfigurationException.class,
                    () -> catalog.lookup("Orders", String.class));
            assertTrue(factory.getMessage().contains("example.QueueFactory"));
            directory.result = Integer.valueOf(42);
            NamingException type = assertThrows(NamingException.class, () -> catalog.lookup("Orders", String.class));
            assertTrue(type.getMessage().contains("java.lang.Integer"));
            assertTrue(type.getMessage().contains("java.lang.String"));
        }
    }

    @Test void hostOnlyUrlReportsTheMissingCatalogPath() throws NamingException {
        Directory directory = new Directory();
        directory.base = "";
        try (MqCatalog catalog = new MqCatalog(new Directory(), directory)) {
            ConfigurationException error = assertThrows(ConfigurationException.class,
                    () -> catalog.lookup("Orders", String.class));
            assertTrue(error.getMessage().contains("/ou=mq,dc=messaging"));
            assertNull(directory.lookupName);
        }
    }
}
