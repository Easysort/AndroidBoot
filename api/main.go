package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

type Metrics struct {
	CPUUsage float64        `json:"cpu_usage_pct"`
	Battery  map[string]any `json:"battery"`
}

func readCPUOnce() (user, nice, system, idle, iowait, irq, softirq, steal uint64, err error) {
	b, err := os.ReadFile("/proc/stat")
	if err != nil {
		return 0, 0, 0, 0, 0, 0, 0, 0, err
	}
	line := strings.SplitN(string(b), "\n", 2)[0] // the "cpu ..." line
	f := strings.Fields(line)
	if len(f) < 5 { // must have at least user nice system idle
		return 0, 0, 0, 0, 0, 0, 0, 0, errors.New("unexpected /proc/stat format")
	}

	// Parse up to the fields that exist; missing ones stay 0.
	dst := []*uint64{&user, &nice, &system, &idle, &iowait, &irq, &softirq, &steal}
	for i := 0; i < len(dst) && 1+i < len(f); i++ {
		v, perr := strconv.ParseUint(f[1+i], 10, 64)
		if perr != nil {
			return 0, 0, 0, 0, 0, 0, 0, 0, fmt.Errorf("parse %q: %w", f[1+i], perr)
		}
		*dst[i] = v
	}
	return
}

func readCPUPercent() (float64, error) {
	u1, n1, s1, id1, io1, ir1, so1, st1, err := readCPUOnce()
	if err != nil {
		return 0, err
	}
	time.Sleep(200 * time.Millisecond)
	u2, n2, s2, id2, io2, ir2, so2, st2, err := readCPUOnce()
	if err != nil {
		return 0, err
	}

	idle1 := id1 + io1
	idle2 := id2 + io2
	nonIdle1 := u1 + n1 + s1 + ir1 + so1 + st1
	nonIdle2 := u2 + n2 + s2 + ir2 + so2 + st2

	total1 := idle1 + nonIdle1
	total2 := idle2 + nonIdle2

	totald := float64(total2 - total1)
	idled := float64(idle2 - idle1)

	if totald <= 0 {
		return 0, errors.New("non-positive CPU delta")
	}
	return (1.0 - idled/totald) * 100.0, nil
}

func readBattery() map[string]any {
	out, err := exec.Command("termux-battery-status").Output()
	if err != nil {
		return map[string]any{
			"error":        err.Error(),
			"hint":         "Install Termux:API and run `pkg install termux-api`",
			"commandUsed":  "termux-battery-status",
		}
	}
	var m map[string]any
	_ = json.Unmarshal(out, &m)
	return m
}

func main() {
	http.HandleFunc("/metrics", func(w http.ResponseWriter, r *http.Request) {
		cpu, err := readCPUPercent()
		if err != nil {
			// Don’t crash the server; report the error and  partial data.
			w.WriteHeader(http.StatusOK)
			_ = json.NewEncoder(w).Encode(map[string]any{
				"cpu_error": err.Error(),
				"battery":   readBattery(),
			})
			return
		}
		_ = json.NewEncoder(w).Encode(Metrics{
			CPUUsage: cpu,
			Battery:  readBattery(),
		})
	})

	// Bind to all interfaces so you can hit it from your laptop over Wi-Fi if needed.
	_ = http.ListenAndServe(":8787", nil)
}
