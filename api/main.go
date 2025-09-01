package main

import (
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"os"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
	"time"
)

type Metrics struct {
	CPUUsagePct *float64       `json:"cpu_usage_pct,omitempty"`
	LoadAvg1    *float64       `json:"load_avg_1,omitempty"`
	Battery     map[string]any `json:"battery"`
	CPUs        int            `json:"cpus"`
	Note        string         `json:"note,omitempty"`
}

// ---------- CPU readers ----------

func readCPUOnce() ([]uint64, error) {
	b, err := os.ReadFile("/proc/stat")
	if err != nil {
		return nil, err
	}
	line := strings.SplitN(string(b), "\n", 2)[0]
	f := strings.Fields(line)
	if len(f) < 5 {
		return nil, errors.New("unexpected /proc/stat format")
	}
	vals := make([]uint64, len(f)-1)
	for i := 1; i < len(f); i++ {
		v, err := strconv.ParseUint(f[i], 10, 64)
		if err != nil {
			return nil, err
		}
		vals[i-1] = v
	}
	return vals, nil
}

func readCPUPercent() (float64, error) {
	v1, err := readCPUOnce()
	if err != nil {
		return 0, err
	}
	time.Sleep(200 * time.Millisecond)
	v2, err := readCPUOnce()
	if err != nil {
		return 0, err
	}
	if len(v1) < 4 || len(v2) < 4 {
		return 0, errors.New("not enough cpu fields")
	}
	idle1 := v1[3]
	idle2 := v2[3]
	total1 := uint64(0)
	total2 := uint64(0)
	for _, x := range v1 {
		total1 += x
	}
	for _, x := range v2 {
		total2 += x
	}
	totald := float64(total2 - total1)
	idled := float64(idle2 - idle1)
	if totald <= 0 {
		return 0, errors.New("invalid delta")
	}
	return (1.0 - idled/totald) * 100.0, nil
}

func readLoadAvg() (float64, error) {
	b, err := os.ReadFile("/proc/loadavg")
	if err != nil {
		return 0, err
	}
	f := strings.Fields(string(b))
	if len(f) < 1 {
		return 0, errors.New("bad /proc/loadavg")
	}
	return strconv.ParseFloat(f[0], 64)
}

// ---------- Battery ----------

func readBattery() map[string]any {
	out, err := exec.Command("termux-battery-status").Output()
	if err != nil {
		return map[string]any{"error": err.Error(), "hint": "install Termux:API (pkg install termux-api)"}
	}
	var m map[string]any
	_ = json.Unmarshal(out, &m)
	return m
}

// ---------- Main ----------

func main() {
	mux := http.NewServeMux()

	mux.HandleFunc("/metrics", func(w http.ResponseWriter, r *http.Request) {
		// CORS headers
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		if r.Header.Get("Access-Control-Request-Private-Network") == "true" {
			w.Header().Set("Access-Control-Allow-Private-Network", "true")
		}
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}

		var m Metrics
		if cpu, err := readCPUPercent(); err == nil {
			m.CPUUsagePct = &cpu
		} else if la, e2 := readLoadAvg(); e2 == nil {
			m.LoadAvg1 = &la
			m.Note = "CPU% unavailable; using 1-min load average instead"
		} else {
			m.Note = "CPU metrics unavailable"
		}
		m.Battery = readBattery()
		m.CPUs = runtime.NumCPU()

		_ = json.NewEncoder(w).Encode(m)
	})

	log.Println("Listening on :8787")
	log.Fatal(http.ListenAndServe(":8787", mux))
}
