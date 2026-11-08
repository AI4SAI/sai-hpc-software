#include <torch/torch.h>
#include <torch/script.h>
#include <iostream>

int main(int argc, char **argv) {
  torch::set_num_threads(1);
  auto x = torch::arange(1, 5, torch::kFloat64).reshape({2, 2});
  auto expected = torch::tensor({{7., 10.}, {15., 22.}}, torch::kFloat64);
  if (!torch::allclose(torch::mm(x, x), expected)) return 2;
  torch::jit::Module model("roundtrip");
  model.define("def forward(self, x):\n    return x * 2.0 + 1.0\n");
  model.save("roundtrip.pt");
  auto loaded = torch::jit::load("roundtrip.pt");
  if (!torch::allclose(loaded.forward({x}).toTensor(), x * 2. + 1.)) return 3;
  if (argc == 2) {
    auto deepks = torch::jit::load(argv[1]);
    if (!deepks.find_method("forward")) return 4;
    std::cout << "SAI_DEEPKS_MODEL_LOAD_PASS\n";
  }
  std::cout << "SAI_LIBTORCH_TENSOR_JIT_PASS " << TORCH_VERSION << "\n";
}
