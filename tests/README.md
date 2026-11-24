# Check a change locally

Run these commands from the repository root. The directory image contains Python and the same LDAP libraries used in the pod. Use the mirrored image reference in a private build. The examples use Docker and a local copy of the image.

```sh
image_ref=$(awk '$1 == "image:" {print $2; exit}' base/deploy/statefulset.yaml)
docker run --rm -i --read-only --network none --entrypoint python3 \
  -v "$PWD:/src:ro" "$image_ref" /src/base/ldap/directory.py validate < base/ldap/mq-objects.ldif
docker run --rm --read-only --network none --entrypoint python3 \
  -v "$PWD:/src:ro" "$image_ref" /src/tests/test_catalog.py -v
```

Replace the input file in the first command to check your own LDIF. The sample prints `Validated 3 catalog entries.` No directory account or running server is needed.

Run the private-access tests with Python, PyYAML and `kubectl` on your path. They copy each template into a temporary work overlay, change the example addresses, then check the rendered rules.

```sh
python3 tests/test_private_overlays.py -v
```

The Maven build runs the Java lookup tests and copies the Tomcat runtime JARs. Use your private Maven settings in work CI.

```sh
mvn -B -f tomcat/pom.xml clean package
```
