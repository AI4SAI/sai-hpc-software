// Dependency probe, not CP2K application acceptance. Host buffers include
// transfer costs. Count successful cuBLAS calls without changing their inputs.
#include <spla/context.h>
#include <spla/gemm.h>
#include <cuda_runtime_api.h>
#include <cublas_v2.h>
#include <dlfcn.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <vector>

static std::atomic<unsigned long long> gpu_calls{0};
extern "C" cublasStatus_t cublasDgemm_v2(
    cublasHandle_t h, cublasOperation_t ta, cublasOperation_t tb,
    int m, int n, int k, const double* alpha, const double* a, int lda,
    const double* b, int ldb, const double* beta, double* c, int ldc) {
    using Func = decltype(&cublasDgemm_v2);
    static auto real = reinterpret_cast<Func>(dlsym(RTLD_NEXT, "cublasDgemm_v2"));
    if (!real) std::abort();
    auto status = real(h, ta, tb, m, n, k, alpha, a, lda, b, ldb, beta, c, ldc);
    if (status == CUBLAS_STATUS_SUCCESS) ++gpu_calls;
    return status;
}

static void check(SplaError status) {
    if (status != SPLA_SUCCESS) throw std::runtime_error("SpLA call failed");
}

int main() try {
    cudaDeviceProp device{};
    if (cudaGetDeviceProperties(&device, 0) != cudaSuccess || device.major != 7 || device.minor != 0)
        throw std::runtime_error("expected a V100/SM70 CUDA device");
    std::cout << std::setprecision(12);
    std::cout << "{\"device\":\"" << device.name << "\",\"sm\":70,\"omp_threads\":\""
              << (std::getenv("OMP_NUM_THREADS") ? std::getenv("OMP_NUM_THREADS") : "unset")
              << "\",\"threshold_flops\":4194304,\"cases\":[\n";
    SplaContext host{}, gpu{};
    check(spla_ctx_create(&host, SPLA_PU_HOST));
    check(spla_ctx_create(&gpu, SPLA_PU_GPU));
    check(spla_ctx_set_op_threshold_gpu(gpu, 128 * 128 * 128 * 2));
    bool first = true;
    for (int n : {64, 256, 1024, 2048}) {
        const size_t count = size_t(n) * n;
        std::vector<double> a(count), b(count), initial(count), reference(count), result(count);
        for (size_t i = 0; i < count; ++i) {
            a[i] = (int((i * 17 + 3) % 127) - 63) / 64.0;
            b[i] = (int((i * 31 + 7) % 131) - 65) / 64.0;
            initial[i] = (int(i % 19) - 9) / 32.0;
        }
        double max_error = 0;
        auto run = [&](SplaContext ctx, std::vector<double>& out) {
            out = initial;
            auto start = std::chrono::steady_clock::now();
            check(spla_dgemm(SPLA_OP_TRANSPOSE, SPLA_OP_NONE, n, n, n, 1.25,
                            a.data(), n, b.data(), n, -0.25, out.data(), n, ctx));
            if (cudaDeviceSynchronize() != cudaSuccess) throw std::runtime_error("CUDA execution failed");
            return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
        };
        auto compare = [&]() {
            double scale = 1;
            for (size_t i = 0; i < count; ++i) {
                if (!std::isfinite(reference[i]) || !std::isfinite(result[i]))
                    throw std::runtime_error("nonfinite output");
                scale = std::max(scale, std::abs(reference[i]));
                max_error = std::max(max_error, std::abs(reference[i] - result[i]));
            }
            if (max_error > 5e-12 * scale) throw std::runtime_error("CPU/GPU mismatch");
        };
        run(host, reference);
        run(gpu, result);
        compare();
        // Independent scalar checks guard against an all-zero/common bad result.
        for (int row : {0, n / 2, n - 1}) for (int col : {0, n / 2, n - 1}) {
            long double sum = 0;
            for (int k = 0; k < n; ++k) sum += (long double)a[k + size_t(row)*n] * b[k + size_t(col)*n];
            double expected = double(1.25L * sum - 0.25L * initial[row + size_t(col)*n]);
            if (std::abs(reference[row + size_t(col)*n] - expected) > 5e-12 * std::max(1.0, std::abs(expected)))
                throw std::runtime_error("scalar reference mismatch");
        }
        std::vector<double> cpu_seconds, gpu_seconds;
        unsigned long long observed_gpu_calls = 0;
        for (int repeat = 0; repeat < 3; ++repeat) {
            for (int order = 0; order < 2; ++order) {
                bool on_gpu = (repeat + order) % 2;
                auto before = gpu_calls.load();
                double seconds = run(on_gpu ? gpu : host, on_gpu ? result : reference);
                auto calls = gpu_calls.load() - before;
                if (!on_gpu && calls) throw std::runtime_error("CPU unexpectedly called cuBLAS");
                if (on_gpu) { gpu_seconds.push_back(seconds); observed_gpu_calls += calls; }
                else cpu_seconds.push_back(seconds);
            }
            compare();
        }
        if ((n == 64 && observed_gpu_calls != 0) || (n > 64 && observed_gpu_calls == 0))
            throw std::runtime_error("GPU offload/CPU threshold path was not observed");
        auto print_times = [](const std::vector<double>& values) {
            std::cout << '[';
            for (size_t i = 0; i < values.size(); ++i) std::cout << (i ? "," : "") << values[i];
            std::cout << ']';
        };
        if (!first) std::cout << ",\n";
        first = false;
        std::cout << "{\"n\":" << n << ",\"transpose\":\"TN\",\"max_abs_error\":" << max_error
                  << ",\"successful_cublas_dgemm_calls\":" << observed_gpu_calls << ",\"cpu_seconds\":";
        print_times(cpu_seconds);
        std::cout << ",\"gpu_seconds\":";
        print_times(gpu_seconds);
        std::cout << '}';
    }
    check(spla_ctx_destroy(&gpu));
    check(spla_ctx_destroy(&host));
    std::cout << "\n],\"passed\":true}\n";
    return 0;
} catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
}
