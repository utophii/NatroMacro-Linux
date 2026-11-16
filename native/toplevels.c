#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wayland-client.h>
#include "toplevel-client.h"

struct item {
    struct ext_foreign_toplevel_handle_v1 *handle;
    char *title, *app_id, *identifier;
    int closed;
};
static struct item items[1024];
static size_t count;
static struct ext_foreign_toplevel_list_v1 *list;

static void closed(void *data, struct ext_foreign_toplevel_handle_v1 *handle) {
    (void)handle;
    ((struct item *)data)->closed = 1;
}
static void done(void *data, struct ext_foreign_toplevel_handle_v1 *handle) { (void)data; (void)handle; }
static void title(void *data, struct ext_foreign_toplevel_handle_v1 *handle, const char *s) {
    (void)handle;
    struct item *item = data;
    free(item->title);
    item->title = strdup(s);
}
static void app_id(void *data, struct ext_foreign_toplevel_handle_v1 *handle, const char *s) {
    (void)handle;
    struct item *item = data;
    free(item->app_id);
    item->app_id = strdup(s);
}
static void identifier(void *data, struct ext_foreign_toplevel_handle_v1 *handle, const char *s) {
    (void)handle;
    struct item *item = data;
    free(item->identifier);
    item->identifier = strdup(s);
}
static const struct ext_foreign_toplevel_handle_v1_listener handle_listener = {closed, done, title, app_id, identifier};
static void toplevel(void *data, struct ext_foreign_toplevel_list_v1 *l, struct ext_foreign_toplevel_handle_v1 *h) {
    (void)data; (void)l;
    if (count == 1024) { ext_foreign_toplevel_handle_v1_destroy(h); return; }
    struct item *item = &items[count++];
    item->handle = h;
    ext_foreign_toplevel_handle_v1_add_listener(h, &handle_listener, item);
}
static void finished(void *data, struct ext_foreign_toplevel_list_v1 *l) { (void)data; (void)l; }
static const struct ext_foreign_toplevel_list_v1_listener list_listener = {toplevel, finished};
static void global(void *data, struct wl_registry *registry, uint32_t name, const char *interface, uint32_t version) {
    (void)data; (void)version;
    if (!strcmp(interface, "ext_foreign_toplevel_list_v1")) {
        list = wl_registry_bind(registry, name, &ext_foreign_toplevel_list_v1_interface, 1);
        ext_foreign_toplevel_list_v1_add_listener(list, &list_listener, NULL);
    }
}
static void removed(void *data, struct wl_registry *r, uint32_t name) { (void)data; (void)r; (void)name; }
static const struct wl_registry_listener registry_listener = {global, removed};
static void json_string(const char *s) {
    putchar('"');
    if (s) for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        if (*p == '"' || *p == '\\') { putchar('\\'); putchar(*p); }
        else if (*p < 32) printf("\\u%04x", *p);
        else putchar(*p);
    }
    putchar('"');
}
int main(void) {
    struct wl_display *display = wl_display_connect(NULL);
    if (!display) { perror("Wayland connection"); return 1; }
    struct wl_registry *registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    for (int i = 0; i < 3; i++) if (wl_display_roundtrip(display) < 0) return 1;
    if (!list) { fputs("ext-foreign-toplevel-list unavailable\n", stderr); return 1; }
    putchar('[');
    int first = 1;
    for (size_t i = 0; i < count; i++) {
        struct item *item = &items[i];
        if (!item->closed && item->identifier) {
            if (!first) putchar(',');
            first = 0;
            fputs("{\"app_id\":", stdout); json_string(item->app_id);
            fputs(",\"title\":", stdout); json_string(item->title);
            fputs(",\"identifier\":", stdout); json_string(item->identifier);
            putchar('}');
        }
        free(item->title); free(item->app_id); free(item->identifier);
        ext_foreign_toplevel_handle_v1_destroy(item->handle);
    }
    puts("]");
    ext_foreign_toplevel_list_v1_destroy(list);
    wl_display_disconnect(display);
    return 0;
}
