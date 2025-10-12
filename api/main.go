package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io/fs"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"math"
	"strconv"
	"strings"
	"time"
)

type M map[string]any

// ---- device id ----
func mustReadID() string {
	b, err := os.ReadFile("device_id.txt")
	if err != nil {
		return "unknown-device"
	}
	return strings.TrimSpace(string(b))
}

var deviceID = mustReadID()

// ---- battery via termux-api ----
func battery() M {
	out, err := execOut("termux-battery-status")
	if err != nil {
		return M{"error": err.Error()}
	}
	var m M
	_ = json.Unmarshal([]byte(out), &m)
	return M{
		"raw":        m,
		"percent":    m["percentage"],
		"charging":   chargingFrom(m),
		"temp_c":     m["temperature"],
		"plugged":    m["plugged"],
		"health":     m["health"],
		"status":     m["status"],
	}
}

func chargingFrom(m M) bool {
	if v, ok := m["plugged"].(string); ok {
		return v != "" && strings.ToUpper(v) != "UNPLUGGED"
	}
	return false
}

// ---- CPU percent from /proc/stat ----
func cpuPercent() float64 {
	aTot, aIdle := readProcStat()
	time.Sleep(250 * time.Millisecond)
	bTot, bIdle := readProcStat()
	dTot := float64(bTot - aTot)
	dIdle := float64(bIdle - aIdle)
	if dTot <= 0 {
		return 0
	}
	p := (1.0 - dIdle/dTot) * 100.0
	if p < 0 {
		p = 0
	}
	if p > 100 {
		p = 100
	}
	return math.Round(p*10) / 10
}

func readProcStat() (total, idle uint64) {
	b, err := os.ReadFile("/proc/stat")
	if err != nil {
		return
	}
	for _, ln := range strings.Split(string(b), "\n") {
		if strings.HasPrefix(ln, "cpu ") {
			f := fieldsU64(ln)
			for i := 0; i < len(f) && i < 8; i++ {
				total += f[i]
			}
			if len(f) > 4 {
				idle = f[3] + f[4] // idle + iowait
			}
			return
		}
	}
	return
}

func fieldsU64(ln string) []uint64 {
	parts := strings.Fields(ln)[1:]
	out := make([]uint64, 0, len(parts))
	for _, p := range parts {
		v, _ := strconv.ParseUint(p, 10, 64)
		out = append(out, v)
	}
	return out
}

// ---- storage via statfs ----
func statfs(path string) (total, free uint64, err error) {
	var s syscall.Statfs_t
	if err = syscall.Statfs(path, &s); err != nil {
		return
	}
	bsize := uint64(s.Bsize)
	total = bsize * uint64(s.Blocks)
	free  = bsize * uint64(s.Bavail)
	return
}

func storage() M {
	root := os.Getenv("HOME")
	if root == "" {
		root = "/"
	}
	t, f, err := statfs(root)
	if err != nil {
		return M{"error": err.Error()}
	}
	used := t - f
	pu := 0.0
	if t > 0 {
		pu = (float64(used) / float64(t)) * 100.0
	}
	return M{
		"path":         root,
		"total_bytes":  t,
		"free_bytes":   f,
		"used_bytes":   used,
		"percent_used": math.Round(pu*10) / 10,
	}
}

// ---- CPU thermal from /sys/class/thermal ----
func thermalCPU() (float64, error) {
	base := "/sys/class/thermal"
	var best float64 = math.NaN()
	err := filepath.WalkDir(base, func(p string, d fs.DirEntry, _ error) error {
		if d == nil || !strings.Contains(p, "thermal_zone") {
			return nil
		}
		if strings.HasSuffix(p, "/type") {
			bt, _ := os.ReadFile(p)
			typ := strings.ToLower(strings.TrimSpace(string(bt)))
			if !strings.Contains(typ, "cpu") && !strings.Contains(typ, "soc") && !strings.Contains(typ, "tsens") {
				return nil
			}
			tp := filepath.Dir(p) + "/temp"
			bt2, err := os.ReadFile(tp)
			if err != nil {
				return nil
			}
			s := strings.TrimSpace(string(bt2))
			val, _ := strconv.ParseFloat(s, 64)
			if val > 200 { // millidegC -> degC
				val = val / 1000.0
			}
			if math.IsNaN(best) || val > best {
				best = val
			}
		}
		return nil
	})
	if err != nil {
		return math.NaN(), err
	}
	if math.IsNaN(best) {
		return best, errors.New("no cpu thermal found")
	}
	return best, nil
}

// ---- network info ----
func wifiSSID() string {
	out, err := execOut("termux-wifi-connectioninfo")
	if err != nil {
		return ""
	}
	var m M
	if err := json.Unmarshal([]byte(out), &m); err != nil {
		return ""
	}
	if s, ok := m["ssid"].(string); ok {
		return s
	}
	return ""
}

func hotspotLikelyOn() bool {
	out, err := suOut("dumpsys connectivity tethering")
	if err != nil || out == "" {
		out, _ = suOut("dumpsys tethering")
	}
	if out == "" {
		return false
	}
	l := strings.ToLower(out)
	return strings.Contains(l, "tethered") || strings.Contains(l, "softap") ||
		(strings.Contains(l, "wifi") && strings.Contains(l, "tether"))
}

// ---- runners ----
func execOut(cmd string, args ...string) (string, error) {
	c := exec.Command(cmd, args...)
	var out bytes.Buffer
	c.Stdout = &out
	c.Stderr = &out
	err := c.Run()
	return strings.TrimSpace(out.String()), err
}

func suOut(cmd string) (string, error) {
	return execOut("su", "-c", cmd)
}

// ---- aggregate + serve ----
func metrics() M {
	bat := battery()
	cpu := cpuPercent()
	st := storage()
	cpuT, _ := thermalCPU()
	ssid := wifiSSID()

	return M{
		"device_id": deviceID,
		"ts":        time.Now().UTC().Format(time.RFC3339),
		"battery":   bat,
		"cpu":       M{"percent": cpu},
		"storage":   st,
		"temps":     M{"battery_c": bat["temp_c"], "cpu_c": cpuT},
		"net":       M{"ssid": ssid, "hotspot_on": hotspotLikelyOn()},
	}
}

func main() {
	port := os.Getenv("GO_API_PORT")
	if port == "" {
		port = "8787"
	}
	http.HandleFunc("/metrics", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Content-Type", "application/json")
		if r.Method == "OPTIONS" {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		json.NewEncoder(w).Encode(metrics())
	})
    http.HandleFunc("/photo", func(w http.ResponseWriter, r *http.Request) {
        w.Header().Set("Access-Control-Allow-Origin", "*")
        if r.Method == http.MethodOptions {
            w.WriteHeader(http.StatusNoContent)
            return
        }
        if r.Method != http.MethodGet {
            w.Header().Set("Content-Type", "application/json")
            http.Error(w, `{"error":"method not allowed"}`, http.StatusMethodNotAllowed)
            return
        }

        // create temp file path for the captured photo
        tmp, err := os.CreateTemp("", "photo-*.jpg")
        if err != nil {
            w.Header().Set("Content-Type", "application/json")
            http.Error(w, `{"error":"failed to create temp file"}`, http.StatusInternalServerError)
            return
        }
        tmpPath := tmp.Name()
        _ = tmp.Close()
        defer os.Remove(tmpPath)

        // capture using termux-api
        if _, err := execOut("termux-camera-photo", tmpPath); err != nil {
            w.Header().Set("Content-Type", "application/json")
            http.Error(w, `{"error":"failed to capture photo. ensure termux-api is installed and camera permission granted"}`, http.StatusInternalServerError)
            return
        }

        // read and return the image
        b, err := os.ReadFile(tmpPath)
        if err != nil {
            w.Header().Set("Content-Type", "application/json")
            http.Error(w, `{"error":"failed to read captured image"}`, http.StatusInternalServerError)
            return
        }
        w.Header().Set("Cache-Control", "no-store")
        w.Header().Set("Content-Type", "image/jpeg")
        w.WriteHeader(http.StatusOK)
        _, _ = w.Write(b)
    })
	_ = http.ListenAndServe(":"+port, nil)
}
