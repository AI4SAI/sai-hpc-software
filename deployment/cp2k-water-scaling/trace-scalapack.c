/* Diagnostic interposer only; not loaded for performance measurements. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <mpi.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

typedef void (*solve_fn)(char *, char *, int *, double *, int *, int *, int *,
                        double *, double *, int *, int *, int *, double *, int *,
                        int *, int *, int *, size_t, size_t);
typedef void (*grid_fn)(int, int *, int *, int *, int *);

void pdsyevd_(char *jobz, char *uplo, int *n, double *a, int *ia, int *ja, int *desca,
              double *w, double *z, int *iz, int *jz, int *descz, double *work,
              int *lwork, int *iwork, int *liwork, int *info, size_t jobz_len, size_t uplo_len)
{
    static solve_fn solve;
    static grid_fn grid;
    static int sequence;
    if (!solve) solve = (solve_fn)dlsym(RTLD_NEXT, "pdsyevd_");
    if (!grid) grid = (grid_fn)dlsym(RTLD_NEXT, "Cblacs_gridinfo");
    if (!solve || !grid) abort();
    int rank, size, rows, cols, row, col;
    PMPI_Comm_rank(MPI_COMM_WORLD, &rank);
    PMPI_Comm_size(MPI_COMM_WORLD, &size);
    grid(desca[1], &rows, &cols, &row, &col);
    double start = PMPI_Wtime();
    int query = *lwork == -1 || *liwork == -1;
    solve(jobz, uplo, n, a, ia, ja, desca, w, z, iz, jz, descz, work, lwork, iwork, liwork, info, jobz_len, uplo_len);
    double elapsed = PMPI_Wtime() - start;
    const char *directory = getenv("SAI_SCALAPACK_TRACE");
    if (!directory) abort();
    char path[4096];
    int length = snprintf(path, sizeof(path), "%s/scalapack-rank-%d.jsonl", directory, rank);
    if (length < 0 || (size_t)length >= sizeof(path)) abort();
    FILE *out = fopen(path, "a");
    if (!out) abort();
    fprintf(out, "{\"sequence\":%d,\"rank\":%d,\"world_size\":%d,\"n\":%d,"
                 "\"grid_rows\":%d,\"grid_cols\":%d,\"row\":%d,\"col\":%d,"
                 "\"query\":%s,\"info\":%d,\"seconds\":%.9f}\n",
            sequence++, rank, size, *n, rows, cols, row, col, query ? "true" : "false", *info, elapsed);
    if (fclose(out)) abort();
}
