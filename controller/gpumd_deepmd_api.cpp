// Compile and link against the selected dependency before the CUDA build.
// Do not execute: inference needs a model and a GPU allocation.
#include "DeepPot.h"
#include <cstdint>

void gpumd_deepmd_api(deepmd::DeepPot& pot, double* output,
                      const std::int64_t* atype, const std::uint32_t* source,
                      const float* edges, const std::int64_t* rows,
                      const std::int64_t* order, const double* coord,
                      const double* box, int n, int storage) {
  (void)deepmd::canonical_index_width(static_cast<std::int64_t>(n));
  (void)pot.has_long_range();
  pot.compute_canonical_graph_gpu(output, output, nullptr, output, atype,
      source, DP_CANONICAL_INDEX32, edges, rows, rows, order, coord,
      nullptr, nullptr, box, static_cast<std::int64_t>(n),
      static_cast<std::int64_t>(n), static_cast<std::int64_t>(storage), nullptr);
}

int main() { return 0; }
