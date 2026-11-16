CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -Werror
PKG_CFLAGS := $(shell pkg-config --cflags wayland-client xkbcommon)
PKG_LIBS := $(shell pkg-config --libs wayland-client xkbcommon)

all: ../natro_linux/bin/natro-input ../natro_linux/bin/natro-toplevels ../natro_linux/bin/libnatrovision.so

build:
	mkdir -p build

build/%-client.h: protocol/%.xml | build
	wayland-scanner client-header $< $@

build/%-protocol.c: protocol/%.xml | build
	wayland-scanner private-code $< $@

../natro_linux/bin/natro-input: input.c build/keyboard-client.h build/pointer-client.h build/keyboard-protocol.c build/pointer-protocol.c
	mkdir -p ../natro_linux/bin
	$(CC) $(CFLAGS) $(PKG_CFLAGS) -Ibuild input.c build/keyboard-protocol.c build/pointer-protocol.c $(PKG_LIBS) -o $@

../natro_linux/bin/natro-toplevels: toplevels.c build/toplevel-client.h build/toplevel-protocol.c
	mkdir -p ../natro_linux/bin
	$(CC) $(CFLAGS) $(PKG_CFLAGS) -Ibuild toplevels.c build/toplevel-protocol.c $(PKG_LIBS) -o $@

clean:
	rm -rf build ../natro_linux/bin/natro-input ../natro_linux/bin/natro-toplevels ../natro_linux/bin/libnatrovision.so

.PHONY: all clean

../natro_linux/bin/libnatrovision.so: vision.c
	mkdir -p ../natro_linux/bin
	$(CC) $(CFLAGS) -fPIC -shared vision.c -o $@
