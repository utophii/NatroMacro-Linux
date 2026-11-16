#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>

static inline int near(const uint8_t *a, const uint8_t *b, int tolerance) {
    return abs((int)a[0] - b[0]) <= tolerance &&
           abs((int)a[1] - b[1]) <= tolerance &&
           abs((int)a[2] - b[2]) <= tolerance;
}

/* Positions use pixel coordinates. Both input arrays are contiguous RGBA. */
int natro_find(const uint8_t *image, int width, int height,
               const uint8_t *pattern, int pw, int ph, int tolerance,
               int limit, int *positions) {
    if (!image || !pattern || !positions || width < 1 || height < 1 || pw < 1 || ph < 1 ||
        pw > width || ph > height || tolerance < 0 || tolerance > 255 || limit < 1) return 0;
    size_t size = (size_t)pw * ph;
    size_t *opaque = malloc(size * sizeof(size_t));
    if (!opaque) return -1;
    size_t n = 0;
    for (size_t i = 0; i < size; i++) if (pattern[i*4+3]) opaque[n++] = i;
    if (!n) { free(opaque); return 0; }
    size_t anchors[4] = {opaque[0], opaque[n/3], opaque[2*n/3], opaque[n-1]};
    int found = 0;
    for (int y = 0; y <= height-ph; y++) {
        for (int x = 0; x <= width-pw; x++) {
            int matches = 1;
            for (int k = 0; k < 4; k++) {
                size_t a = anchors[k];
                size_t at = ((size_t)(y + a/pw)*width + x + a%pw)*4;
                if (!near(image+at, pattern+a*4, tolerance)) { matches = 0; break; }
            }
            if (!matches) continue;
            for (size_t k = 0; k < n; k++) {
                size_t a = opaque[k];
                size_t at = ((size_t)(y + a/pw)*width + x + a%pw)*4;
                if (!near(image+at, pattern+a*4, tolerance)) { matches = 0; break; }
            }
            if (matches) {
                positions[found*2] = x;
                positions[found*2+1] = y;
                if (++found == limit) { free(opaque); return found; }
            }
        }
    }
    free(opaque);
    return found;
}
