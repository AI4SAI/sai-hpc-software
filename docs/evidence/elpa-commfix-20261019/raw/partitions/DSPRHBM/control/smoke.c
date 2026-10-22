/* Analytic Hermitian spectrum, eigenvector residual and orthogonality.
 * MPI block-cyclic row distribution; CPU/GPU and both ELPA solvers. */
#include <complex.h>
#include <math.h>
#include <mpi.h>
#include <stdio.h>
#include <stdlib.h>
#include <elpa/elpa.h>

static int rank_id;
static void check(int code, const char *where) {
    if (code != ELPA_OK) {
        fprintf(stderr, "rank %d: %s: %d\n", rank_id, where, code);
        MPI_Abort(MPI_COMM_WORLD, 1);
    }
}
static void set(elpa_t e, const char *name, int value) {
    int error;
    elpa_set(e, name, value, &error);
    check(error, name);
}
int main(int argc, char **argv) {
    int provided, ranks, error;
    MPI_Init_thread(&argc, &argv, MPI_THREAD_FUNNELED, &provided);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank_id);
    MPI_Comm_size(MPI_COMM_WORLD, &ranks);
    if ((argc != 4 && argc != 5) || provided < MPI_THREAD_FUNNELED) MPI_Abort(MPI_COMM_WORLD, 2);
    int gpu = atoi(argv[1]), solver = atoi(argv[2]), is_complex = atoi(argv[3]);
    int nonblocking = argc == 5 ? atoi(argv[4]) : 0;
    const int n = 128, nb = 16;
    if (n % (nb * ranks)) MPI_Abort(MPI_COMM_WORLD, 3);
    int nr = n / ranks;
    check(elpa_init(20221109), "init");
    const char *repeats_env = getenv("ELPA_OBJECT_REPEATS");
    char *end = NULL;
    long objects = repeats_env ? strtol(repeats_env, &end, 10) : 1;
    if (objects < 1 || objects > 128 || (repeats_env && (end == repeats_env || *end)))
        MPI_Abort(MPI_COMM_WORLD, 5);
    for (long allocation = 0; allocation < objects; ++allocation) {
    elpa_t e = elpa_allocate(&error);
    check(error, "allocate");
    set(e, "na", n); set(e, "nev", n); set(e, "nblk", nb);
    set(e, "local_nrows", nr); set(e, "local_ncols", n);
    set(e, "mpi_comm_parent", (int)MPI_Comm_c2f(MPI_COMM_WORLD));
    set(e, "process_row", rank_id); set(e, "process_col", 0);
    set(e, "omp_threads", 2);
    check(elpa_setup(e), "setup");
    set(e, "solver", solver);
    set(e, "nbc_row_elpa2_band_to_full", nonblocking);
    set(e, "nbc_col_elpa2_tridi_to_band", nonblocking);
    set(e, "nvidia-gpu", gpu);
    if (gpu) {
        if (solver == ELPA_SOLVER_2STAGE)
            set(e, is_complex ? "complex_kernel" : "real_kernel", is_complex ?
                ELPA_2STAGE_COMPLEX_NVIDIA_GPU : ELPA_2STAGE_REAL_NVIDIA_GPU);
        set(e, "use_gpu_id", 0);
        check(elpa_setup_gpu(e), "setup_gpu");
    }
    double *eval = calloc(n, sizeof(double));
    double complex gamma = is_complex ? -0.3 + 0.4 * I : -0.5;
    for (int repeat = 0; repeat < 2; ++repeat) {
        double complex *qall = calloc(n*n, sizeof(double complex));
        if (is_complex) {
            double complex *a = calloc(nr*n, sizeof(double complex));
            double complex *q = calloc(nr*n, sizeof(double complex));
            for (int j = 0; j < n; ++j) for (int i = 0; i < nr; ++i) {
                int g = (i/nb*ranks + rank_id)*nb + i%nb;
                a[i+j*nr] = g == j ? 2.0 : (g == j-1 ? gamma : (g == j+1 ? conj(gamma) : 0));
            }
            elpa_eigenvectors_double_complex(e, a, eval, q, &error);
            check(error, "complex eigenvectors");
            for (int j = 0; j < n; ++j) for (int i = 0; i < nr; ++i)
                qall[(i/nb*ranks+rank_id)*nb+i%nb+j*n] = q[i+j*nr];
            free(a); free(q);
        } else {
            double *a = calloc(nr*n, sizeof(double)), *q = calloc(nr*n, sizeof(double));
            for (int j = 0; j < n; ++j) for (int i = 0; i < nr; ++i) {
                int g = (i/nb*ranks + rank_id)*nb + i%nb;
                a[i+j*nr] = g == j ? 2.0 : (abs(g-j) == 1 ? -0.5 : 0);
            }
            elpa_eigenvectors_double(e, a, eval, q, &error);
            check(error, "real eigenvectors");
            for (int j = 0; j < n; ++j) for (int i = 0; i < nr; ++i)
                qall[(i/nb*ranks+rank_id)*nb+i%nb+j*n] = q[i+j*nr];
            free(a); free(q);
        }
        MPI_Allreduce(MPI_IN_PLACE, qall, n*n, MPI_C_DOUBLE_COMPLEX, MPI_SUM, MPI_COMM_WORLD);
        double spectrum = 0, residual = 0, orthogonality = 0;
        for (int j = 0; j < n; ++j) {
            spectrum = fmax(spectrum, fabs(eval[j] - (2.0-cos((j+1)*acos(-1.0)/(n+1)))));
            for (int i = 0; i < n; ++i) {
                double complex r = (2.0-eval[j])*qall[i+j*n];
                if (i) r += conj(gamma)*qall[i-1+j*n];
                if (i+1 < n) r += gamma*qall[i+1+j*n];
                residual = fmax(residual, cabs(r));
                double complex dot = 0;
                for (int k = 0; k < n; ++k) dot += conj(qall[k+i*n])*qall[k+j*n];
                orthogonality = fmax(orthogonality, cabs(dot-(i==j ? 1.0 : 0.0)));
            }
        }
        int ok = isfinite(spectrum) && isfinite(residual) && isfinite(orthogonality) &&
            spectrum < 1e-10 && residual < 1e-10 && orthogonality < 1e-10;
        /* fmax can suppress NaNs, so explicitly reject every non-finite result. */
        for (int i = 0; i < n; ++i) ok &= isfinite(eval[i]);
        for (int i = 0; i < n*n; ++i) ok &= isfinite(creal(qall[i])) && isfinite(cimag(qall[i]));
        if (!rank_id) printf("ELPA_NUMERIC %s ranks=%d gpu=%d solver=%d complex=%d nonblocking=%d repeat=%d spectrum=%.3e residual=%.3e orthogonality=%.3e\n",
            ok ? "PASS" : "FAIL", ranks, gpu, solver, is_complex, nonblocking, repeat, spectrum, residual, orthogonality);
        if (!ok) MPI_Abort(MPI_COMM_WORLD, 4);
        free(qall);
    }
    elpa_deallocate(e, &error); check(error, "deallocate");
    free(eval);
    }
    elpa_uninit(&error); check(error, "uninit");
    MPI_Finalize();
    return 0;
}
