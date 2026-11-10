/* Native MPI/CUDA allocator-recycling reproducer; no LAMMPS or container. */
#include <mpi.h>
#include <cuda_runtime_api.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CUDA(call) do { cudaError_t e = (call); if (e != cudaSuccess) { \
    fprintf(stderr, "%s: %s\n", #call, cudaGetErrorString(e)); MPI_Abort(MPI_COMM_WORLD, 2); } } while (0)

static void verify(void *gpu, size_t bytes, int value)
{
    unsigned char *host = malloc(bytes);
    CUDA(cudaMemcpy(host, gpu, bytes, cudaMemcpyDeviceToHost));
    for (size_t i = 0; i < bytes; ++i) if (host[i] != (unsigned char)value) {
        fprintf(stderr, "data mismatch at %zu: %u != %u\n", i, host[i], (unsigned char)value);
        MPI_Abort(MPI_COMM_WORLD, 3);
    }
    free(host);
}

int main(int argc, char **argv)
{
    int rank, size, overlap_count = 0;
    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &size);
    if (size != 2) MPI_Abort(MPI_COMM_WORLD, 1);
    CUDA(cudaSetDevice(0));
    void *recv;
    CUDA(cudaMalloc(&recv, 65536));
    for (int iteration = 0; iteration < 30; ++iteration) {
        void *lower = NULL, *old = NULL, *upper = NULL, *replacement = NULL;
        uintptr_t old_address = 0;
        if (rank == 0) {
            CUDA(cudaMalloc(&lower, 32768));
            CUDA(cudaMalloc(&old, 32768));
            CUDA(cudaMalloc(&upper, 32768));
            old_address = (uintptr_t)old;
            CUDA(cudaMemset(old, iteration + 1, 32768));
            CUDA(cudaDeviceSynchronize());
            MPI_Send(old, 32768, MPI_BYTE, 1, 1, MPI_COMM_WORLD);
        } else {
            MPI_Recv(recv, 32768, MPI_BYTE, 0, 1, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
            verify(recv, 32768, iteration + 1);
        }
        MPI_Barrier(MPI_COMM_WORLD);
        if (rank == 0) {
            CUDA(cudaFree(old));
            CUDA(cudaFree(lower));
            CUDA(cudaMalloc(&replacement, 65536));
            uintptr_t new_address = (uintptr_t)replacement;
            int overlap = new_address < old_address && new_address + 65536 > old_address;
            overlap_count += overlap;
            printf("ITERATION %d old=%p size=32768 replacement=%p size=65536 lower_overlap=%d\n",
                   iteration, (void *)old_address, replacement, overlap);
            CUDA(cudaMemset(replacement, iteration + 101, 65536));
            CUDA(cudaDeviceSynchronize());
            MPI_Send(replacement, 65536, MPI_BYTE, 1, 2, MPI_COMM_WORLD);
        } else {
            MPI_Recv(recv, 65536, MPI_BYTE, 0, 2, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
            verify(recv, 65536, iteration + 101);
        }
        MPI_Barrier(MPI_COMM_WORLD);
        if (rank == 0) {
            CUDA(cudaFree(replacement));
            CUDA(cudaFree(upper));
        }
    }
    if (rank == 0) printf("PASS exact data checks; lower_overlap_count=%d\n", overlap_count);
    CUDA(cudaFree(recv));
    MPI_Finalize();
    return 0;
}
