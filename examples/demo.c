#include <stdlib.h>
#include <stdio.h>

/* Intentionally broken sample; never use this code in production. */
static void invalid_write(void)
{
    int *p = malloc(sizeof(int));
    if (!p) return;
    p[1] = 42; /* InvalidWrite: one element past the allocation. */
    free(p);
}

static void leak(void)
{
    volatile char *lost = malloc(64);
    if (lost) lost[0] = 'x';
}

int main(void)
{
    for (int i = 0; i < 3; ++i) invalid_write();
    leak();
    puts("Demo finished (contains intentional memory errors).");
    return 0;
}
