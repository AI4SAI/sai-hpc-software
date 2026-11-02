package driver

import (
	"os/exec"
	"testing"
	"time"

	"github.com/apptainer/apptainer/pkg/image"
)

// A helper that has been unmounted can still be finishing its final fsync.
// The old one-second deadline kills this otherwise successful helper.
func TestSAIGracefulStopLetsSlowHelperFinish(t *testing.T) {
	cmd := exec.Command("/bin/sleep", "2")
	instance := startSAIHelper(t, cmd)
	feature := fuseappsFeature{binName: "test-helper", instances: []*fuseappsInstance{instance}}
	if err := feature.stop("test-target", false); err != nil {
		t.Fatal(err)
	}
	if !cmd.ProcessState.Success() {
		t.Fatalf("graceful helper was terminated: %v", cmd.ProcessState)
	}
}

func TestSAIExplicitTerminationStillStopsPromptly(t *testing.T) {
	cmd := exec.Command("/bin/sleep", "60")
	instance := startSAIHelper(t, cmd)
	feature := fuseappsFeature{binName: "test-helper", instances: []*fuseappsInstance{instance}}
	started := time.Now()
	if err := feature.stop("test-target", true); err != nil {
		t.Fatal(err)
	}
	if elapsed := time.Since(started); elapsed > 4*time.Second {
		t.Fatalf("explicit termination took %s", elapsed)
	}
	if cmd.ProcessState.Success() {
		t.Fatal("expected explicit termination")
	}
}

func startSAIHelper(t *testing.T, cmd *exec.Cmd) *fuseappsInstance {
	t.Helper()
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = cmd.Process.Kill() })
	stdout, stderr := make(chan error, 1), make(chan error, 1)
	stdout <- nil
	stderr <- nil
	return &fuseappsInstance{cmd: cmd, params: &image.MountParams{Target: "test-target"},
		stdout: fuseappsFDescript{err: stdout}, stderr: fuseappsFDescript{err: stderr}}
}
