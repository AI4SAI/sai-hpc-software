/* Standalone ISA probe: no CP2K, matrix, eigensolver or GPU kernel. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <mpi.h>
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc != 2) return 2;
    MPI_Init(&argc, &argv);
    void *lib = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!lib) { fprintf(stderr, "%s\n", dlerror()); return 3; }
    int (*init)(int) = dlsym(lib, "elpa_init");
    void *(*allocate)(int *) = dlsym(lib, "elpa_allocate");
    void (*deallocate)(void *, int *) = dlsym(lib, "elpa_deallocate");
    if (!init || !allocate || !deallocate) return 4;
    Dl_info info;
    if (!dladdr((void *)allocate, &info)) return 5;
    printf("library=%s base=%p allocate=%p\n", info.dli_fname,
           info.dli_fbase, (void *)allocate);
    /* From this installed library's elpa_version.h. */
    int result = init(20260202);
    printf("elpa_init(20260202)=%d\n", result);
    if (result) return 6;
    int error = -999;
    puts("calling elpa_allocate; no kernel has been selected");
    void *obj = allocate(&error);
    printf("elpa_allocate=%p error=%d\n", obj, error);
    if (!obj || error) return 7;
    deallocate(obj, &error);
    printf("elpa_deallocate error=%d\n", error);
    MPI_Finalize();
    return error ? 8 : 0;
}
