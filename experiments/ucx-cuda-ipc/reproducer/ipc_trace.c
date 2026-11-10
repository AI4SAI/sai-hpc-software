/* Observational CUDA IPC tracer. No return values or pointer values are changed. */
#define _GNU_SOURCE
#include <cuda.h>
#include <dlfcn.h>
#include <execinfo.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static FILE *stream;
static unsigned long sequence;
#define SYMBOL_STRING_IMPL(name) #name
#define SYMBOL_STRING(name) SYMBOL_STRING_IMPL(name)

static FILE *log_stream(void)
{
    const char *directory = getenv("SAI_UCX_TRACE_PATH");
    if (!directory) return NULL;
    if (!stream) {
        char path[4096];
        snprintf(path, sizeof(path), "%s/ipc-r%s-p%d.log", directory,
                 getenv("OMPI_COMM_WORLD_RANK") ? getenv("OMPI_COMM_WORLD_RANK") : "none", getpid());
        stream = fopen(path, "a");
        if (stream) setvbuf(stream, NULL, _IOLBF, 0);
    }
    return stream;
}

static void record(const char *operation, CUresult result, CUdeviceptr address,
                   const CUipcMemHandle *handle, void *caller)
{
    pthread_mutex_lock(&lock);
    FILE *out = log_stream();
    if (out) {
        CUcontext context = NULL;
        CUdevice device = -1;
        CUresult (*get_context)(CUcontext *) = dlsym(RTLD_NEXT, "cuCtxGetCurrent");
        CUresult (*get_device)(CUdevice *) = dlsym(RTLD_NEXT, "cuCtxGetDevice");
        if (get_context) get_context(&context);
        if (get_device && context) get_device(&device);
        Dl_info info = {0};
        dladdr(caller, &info);
        fprintf(out, "%lu %s result=%d address=0x%llx context=%p device=%d caller=%s+0x%lx handle=",
                ++sequence, operation, result, (unsigned long long)address, (void *)context, device,
                info.dli_fname ? info.dli_fname : "?", (unsigned long)((char *)caller - (char *)info.dli_fbase));
        if (handle) for (size_t i = 0; i < sizeof(*handle); ++i)
            fprintf(out, "%02x", ((const unsigned char *)handle)[i]);
        fprintf(out, "\n");
        if (result != CUDA_SUCCESS) {
            void *frames[32];
            int n = backtrace(frames, 32);
            for (int i = 0; i < n; ++i) {
                Dl_info frame = {0};
                dladdr(frames[i], &frame);
                fprintf(out, "  frame %d %s+0x%lx %s\n", i,
                        frame.dli_fname ? frame.dli_fname : "?",
                        (unsigned long)((char *)frames[i] - (char *)frame.dli_fbase),
                        frame.dli_sname ? frame.dli_sname : "?");
            }
        }
    }
    pthread_mutex_unlock(&lock);
}

CUresult CUDAAPI cuIpcOpenMemHandle(CUdeviceptr *address, CUipcMemHandle handle, unsigned int flags)
{
    CUresult (*fn)(CUdeviceptr *, CUipcMemHandle, unsigned int) = dlsym(RTLD_NEXT, SYMBOL_STRING(cuIpcOpenMemHandle));
    if (!fn) abort();
    CUresult result = fn(address, handle, flags);
    record("open", result, result == CUDA_SUCCESS ? *address : 0, &handle, __builtin_return_address(0));
    return result;
}

CUresult CUDAAPI cuIpcCloseMemHandle(CUdeviceptr address)
{
    CUresult (*fn)(CUdeviceptr) = dlsym(RTLD_NEXT, "cuIpcCloseMemHandle");
    if (!fn) abort();
    CUresult result = fn(address);
    record("close", result, address, NULL, __builtin_return_address(0));
    return result;
}

CUresult CUDAAPI cuIpcGetMemHandle(CUipcMemHandle *handle, CUdeviceptr address)
{
    CUresult (*fn)(CUipcMemHandle *, CUdeviceptr) = dlsym(RTLD_NEXT, "cuIpcGetMemHandle");
    if (!fn) abort();
    CUresult result = fn(handle, address);
    record("export", result, address, result == CUDA_SUCCESS ? handle : NULL, __builtin_return_address(0));
    if (result == CUDA_SUCCESS) {
        CUdeviceptr base = 0;
        size_t size = 0;
        unsigned long long id = 0;
        CUresult (*range)(CUdeviceptr *, size_t *, CUdeviceptr) = dlsym(RTLD_NEXT, "cuMemGetAddressRange_v2");
        CUresult (*attribute)(void *, CUpointer_attribute, CUdeviceptr) = dlsym(RTLD_NEXT, "cuPointerGetAttribute");
        if (range) range(&base, &size, address);
        if (attribute) attribute(&id, CU_POINTER_ATTRIBUTE_BUFFER_ID, address);
        pthread_mutex_lock(&lock);
        FILE *out = log_stream();
        if (out) fprintf(out, "  exported_range base=0x%llx size=%zu buffer_id=%llu\n", (unsigned long long)base, size, id);
        pthread_mutex_unlock(&lock);
    }
    return result;
}

__attribute__((destructor)) static void finish(void)
{
    if (!stream) return;
    FILE *maps = fopen("/proc/self/maps", "r");
    if (maps) {
        char line[8192];
        while (fgets(line, sizeof(line), maps))
            if (strstr(line, "/libuc") || strstr(line, "/libcuda") || strstr(line, "/libmpi"))
                fprintf(stream, "MAP %s", line);
        fclose(maps);
    }
    fclose(stream);
}
