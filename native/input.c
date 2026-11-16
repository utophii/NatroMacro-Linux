#define _GNU_SOURCE
#include <errno.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include <wayland-client.h>
#include <xkbcommon/xkbcommon.h>
#include "keyboard-client.h"
#include "pointer-client.h"

static struct wl_display *display;
static struct wl_seat *seat;
static struct zwp_virtual_keyboard_manager_v1 *keyboard_manager;
static struct zwlr_virtual_pointer_manager_v1 *pointer_manager;
static struct zwp_virtual_keyboard_v1 *keyboard;
static struct zwlr_virtual_pointer_v1 *pointer;
static struct xkb_state *key_state;
struct named_output { struct wl_output *output; char name[256]; };
static struct named_output outputs[32];
static unsigned output_count;
static uint32_t pointer_version;
static bool keys[768], buttons[768];
static volatile sig_atomic_t stopping;

static uint32_t now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint32_t)((uint64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000);
}

static void on_signal(int sig) { (void)sig; stopping = 1; }

static void output_geometry(void *d, struct wl_output *o, int32_t x, int32_t y, int32_t pw, int32_t ph, int32_t sub, const char *make, const char *model, int32_t transform) {
    (void)d; (void)o; (void)x; (void)y; (void)pw; (void)ph; (void)sub; (void)make; (void)model; (void)transform;
}
static void output_mode(void *d, struct wl_output *o, uint32_t flags, int32_t w, int32_t h, int32_t rate) {
    (void)d; (void)o; (void)flags; (void)w; (void)h; (void)rate;
}
static void output_done(void *d, struct wl_output *o) { (void)d; (void)o; }
static void output_scale(void *d, struct wl_output *o, int32_t s) { (void)d; (void)o; (void)s; }
static void output_name(void *d, struct wl_output *o, const char *name) {
    (void)o;
    snprintf(((struct named_output *)d)->name, 256, "%s", name);
}
static void output_description(void *d, struct wl_output *o, const char *s) { (void)d; (void)o; (void)s; }
static const struct wl_output_listener output_listener = {output_geometry, output_mode, output_done, output_scale, output_name, output_description};

static void registry_global(void *data, struct wl_registry *r, uint32_t name,
                            const char *interface, uint32_t version) {
    (void)data;
    (void)version;
    if (!strcmp(interface, "wl_seat") && !seat)
        seat = wl_registry_bind(r, name, &wl_seat_interface, 1);
    else if (!strcmp(interface, "zwp_virtual_keyboard_manager_v1"))
        keyboard_manager = wl_registry_bind(r, name, &zwp_virtual_keyboard_manager_v1_interface, 1);
    else if (!strcmp(interface, "zwlr_virtual_pointer_manager_v1")) {
        pointer_version = version < 2 ? version : 2;
        pointer_manager = wl_registry_bind(r, name, &zwlr_virtual_pointer_manager_v1_interface, pointer_version);
    } else if (!strcmp(interface, "wl_output") && version >= 4 && output_count < 32) {
        struct named_output *out = &outputs[output_count++];
        out->output = wl_registry_bind(r, name, &wl_output_interface, 4);
        wl_output_add_listener(out->output, &output_listener, out);
    }
}

static void registry_remove(void *data, struct wl_registry *r, uint32_t name) {
    (void)data; (void)r; (void)name;
}

static const struct wl_registry_listener registry_listener = {registry_global, registry_remove};

static void modifiers(void) {
    zwp_virtual_keyboard_v1_modifiers(keyboard,
        xkb_state_serialize_mods(key_state, XKB_STATE_MODS_DEPRESSED),
        xkb_state_serialize_mods(key_state, XKB_STATE_MODS_LATCHED),
        xkb_state_serialize_mods(key_state, XKB_STATE_MODS_LOCKED),
        xkb_state_serialize_layout(key_state, XKB_STATE_LAYOUT_EFFECTIVE));
}

static void key_event(unsigned code, unsigned down) {
    if (keys[code] == (bool)down) return;
    keys[code] = down;
    zwp_virtual_keyboard_v1_key(keyboard, now_ms(), code, down);
    xkb_state_update_key(key_state, code + 8, down ? XKB_KEY_DOWN : XKB_KEY_UP);
    modifiers();
}

static void button_event(unsigned code, unsigned down) {
    if (buttons[code] == (bool)down) return;
    buttons[code] = down;
    zwlr_virtual_pointer_v1_button(pointer, now_ms(), code, down);
    zwlr_virtual_pointer_v1_frame(pointer);
}

static void release_all(void) {
    for (unsigned i = 0; i < 768; i++) {
        if (keys[i]) key_event(i, 0);
        if (buttons[i]) button_event(i, 0);
    }
    wl_display_flush(display);
}

static int setup(void) {
    display = wl_display_connect(NULL);
    if (!display) { perror("Wayland connection"); return -1; }
    struct wl_registry *registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    if (wl_display_roundtrip(display) < 0) return -1;
    if (!seat || !keyboard_manager || !pointer_manager) {
        fputs("Niri virtual keyboard and pointer protocols are required\n", stderr);
        return -1;
    }
    keyboard = zwp_virtual_keyboard_manager_v1_create_virtual_keyboard(keyboard_manager, seat);
    if (wl_display_roundtrip(display) < 0) return -1;
    const char *wanted = getenv("NATRO_OUTPUT");
    struct wl_output *target = NULL;
    if (wanted) {
        for (unsigned i = 0; i < output_count; i++)
            if (!strcmp(outputs[i].name, wanted)) target = outputs[i].output;
        if (!target || pointer_version < 2) {
            fputs("Cannot map virtual pointer to requested output\n", stderr);
            return -1;
        }
    }
    pointer = target ? zwlr_virtual_pointer_manager_v1_create_virtual_pointer_with_output(pointer_manager, seat, target)
                     : zwlr_virtual_pointer_manager_v1_create_virtual_pointer(pointer_manager, seat);
    struct xkb_context *ctx = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
    if (!ctx) return -1;
    struct xkb_rule_names names = {.layout = "us"};
    struct xkb_keymap *map = xkb_keymap_new_from_names(ctx, &names, XKB_KEYMAP_COMPILE_NO_FLAGS);
    if (!map) { xkb_context_unref(ctx); return -1; }
    key_state = xkb_state_new(map);
    char *text = xkb_keymap_get_as_string(map, XKB_KEYMAP_FORMAT_TEXT_V1);
    if (!key_state || !text) { free(text); xkb_keymap_unref(map); xkb_context_unref(ctx); return -1; }
    size_t len = strlen(text) + 1;
    int fd = memfd_create("natro-keymap", MFD_CLOEXEC);
    if (fd < 0 || ftruncate(fd, (off_t)len) < 0) { free(text); if(fd >= 0) close(fd); return -1; }
    void *mem = mmap(NULL, len, PROT_WRITE, MAP_SHARED, fd, 0);
    if (mem == MAP_FAILED) { close(fd); free(text); return -1; }
    memcpy(mem, text, len);
    munmap(mem, len);
    zwp_virtual_keyboard_v1_keymap(keyboard, WL_KEYBOARD_KEYMAP_FORMAT_XKB_V1, fd, (uint32_t)len);
    close(fd);
    free(text);
    xkb_keymap_unref(map);
    xkb_context_unref(ctx);
    if (wl_display_roundtrip(display) < 0) return -1;
    return 0;
}

static int command(const char *line) {
    unsigned code, state, w, h, x, y;
    int dx, dy;
    char extra;
    if (!strcmp(line, "PING")) return 0;
    if (!strcmp(line, "QUIT")) { stopping = 1; return 0; }
    if (!strcmp(line, "RELEASE")) { release_all(); return 0; }
    if (sscanf(line, "KEY %u %u %c", &code, &state, &extra) == 2 && code < 768 && state < 2) {
        key_event(code, state);
    } else if (sscanf(line, "BUTTON %u %u %c", &code, &state, &extra) == 2 && code >= 272 && code <= 279 && state < 2) {
        button_event(code, state);
    } else if (sscanf(line, "MOVE %d %d %c", &dx, &dy, &extra) == 2 && labs((long)dx) < 1000000 && labs((long)dy) < 1000000) {
        zwlr_virtual_pointer_v1_motion(pointer, now_ms(), wl_fixed_from_int(dx), wl_fixed_from_int(dy));
        zwlr_virtual_pointer_v1_frame(pointer);
    } else if (sscanf(line, "ABS %u %u %u %u %c", &x, &y, &w, &h, &extra) == 4 && w && h && x <= w && y <= h) {
        zwlr_virtual_pointer_v1_motion_absolute(pointer, now_ms(), x, y, w, h);
        zwlr_virtual_pointer_v1_frame(pointer);
    } else if (sscanf(line, "SCROLL %d %c", &dy, &extra) == 1 && dy >= -100 && dy <= 100) {
        zwlr_virtual_pointer_v1_axis_source(pointer, WL_POINTER_AXIS_SOURCE_WHEEL);
        zwlr_virtual_pointer_v1_axis_discrete(pointer, now_ms(), WL_POINTER_AXIS_VERTICAL_SCROLL, wl_fixed_from_int(dy * 10), dy);
        zwlr_virtual_pointer_v1_frame(pointer);
    } else {
        fputs("Invalid input command\n", stderr);
        return -1;
    }
    return wl_display_flush(display) < 0 && errno != EAGAIN ? -1 : 0;
}

int main(void) {
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    signal(SIGPIPE, SIG_IGN);
    setvbuf(stdout, NULL, _IOLBF, 0);
    if (setup() < 0) return 1;
    puts("READY");
    char pending[4096];
    size_t used = 0;
    uint32_t last = now_ms();
    int status = 0;
    while (!stopping) {
        struct pollfd fds[2] = {{STDIN_FILENO, POLLIN, 0}, {wl_display_get_fd(display), POLLIN, 0}};
        if (poll(fds, 2, 100) < 0) { if (errno == EINTR) continue; status = 1; break; }
        if ((uint32_t)(now_ms() - last) > 2000) { fputs("Input watchdog expired\n", stderr); status = 1; break; }
        if (fds[1].revents & (POLLERR | POLLHUP)) { status = 1; break; }
        if ((fds[1].revents & POLLIN) && wl_display_dispatch(display) < 0) { status = 1; break; }
        if (fds[0].revents & (POLLIN | POLLHUP)) {
            ssize_t n = read(STDIN_FILENO, pending + used, sizeof(pending) - used - 1);
            if (n <= 0) break;
            used += (size_t)n;
            pending[used] = '\0';
            char *start = pending, *end;
            while ((end = strchr(start, '\n'))) {
                *end = '\0';
                if (command(start) < 0) { status = 1; stopping = 1; break; }
                last = now_ms();
                puts("OK");
                start = end + 1;
            }
            used -= (size_t)(start - pending);
            memmove(pending, start, used);
            if (used == sizeof(pending) - 1) { status = 1; break; }
        }
    }
    release_all();
    zwp_virtual_keyboard_v1_destroy(keyboard);
    zwlr_virtual_pointer_v1_destroy(pointer);
    wl_display_flush(display);
    xkb_state_unref(key_state);
    wl_display_disconnect(display);
    return status;
}
