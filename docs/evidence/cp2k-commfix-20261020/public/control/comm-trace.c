#define _GNU_SOURCE
#include <dlfcn.h>
#include <mpi.h>
#include <stdio.h>
#include <stdlib.h>
static MPI_Fint live[1024];
static int active, created, freed;
void mpi_comm_split_type_(MPI_Fint *comm, MPI_Fint *kind, MPI_Fint *key,
                         MPI_Fint *info, MPI_Fint *result, MPI_Fint *error) {
    typedef void (*fn)(MPI_Fint*,MPI_Fint*,MPI_Fint*,MPI_Fint*,MPI_Fint*,MPI_Fint*);
    fn real=(fn)dlsym(RTLD_NEXT,"mpi_comm_split_type_");
    if (!real || active == 1024) abort();
    real(comm,kind,key,info,result,error);
    if (*error == MPI_SUCCESS) { live[active++]=*result; ++created; }
}
void mpi_comm_free_(MPI_Fint *comm, MPI_Fint *error) {
    typedef void (*fn)(MPI_Fint*,MPI_Fint*);
    fn real=(fn)dlsym(RTLD_NEXT,"mpi_comm_free_");
    if (!real) abort();
    MPI_Fint previous=*comm;
    real(comm,error);
    if (*error == MPI_SUCCESS) for (int i=0; i<active; ++i) {
        if (live[i] == previous) { live[i]=live[--active]; ++freed; break; }
    }
}
__attribute__((destructor)) static void report(void) {
    fprintf(stderr,"ELPA_COMM_TRACE created=%d explicitly_freed=%d live_at_exit=%d\n",created,freed,active);
}
