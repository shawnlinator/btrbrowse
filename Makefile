PREFIX ?= /usr

# The root helper and its polkit policy must live in root-owned locations.
install-helper:
	install -D -m 755 -o root -g root helper/btrbrowse-helper $(DESTDIR)$(PREFIX)/libexec/btrbrowse-helper
	install -D -m 644 -o root -g root data/org.btrbrowse.helper.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.btrbrowse.helper.policy

install: install-helper
	python3 -m pip install --break-system-packages .

test:
	python3 -m unittest discover -s tests -v

.PHONY: install-helper install test
