#include <stddef.h>

/*
 * DMS-1 freestanding memory primitives.
 * The GCC build links with -nodefaultlibs, while the compiler may still
 * lower structure copies/initialisation to memcpy/memset calls.
 * Keep these implementations dependency-free.
 */
void *memcpy(void *dst, const void *src, size_t n)
{
    unsigned char *d = (unsigned char *)dst;
    const unsigned char *s = (const unsigned char *)src;
    while (n != 0u) {
        *d++ = *s++;
        --n;
    }
    return dst;
}

void *memset(void *dst, int value, size_t n)
{
    unsigned char *d = (unsigned char *)dst;
    const unsigned char v = (unsigned char)value;
    while (n != 0u) {
        *d++ = v;
        --n;
    }
    return dst;
}
