package main

import (
	"encoding/json"
	"net/http"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

func cpuPct() *float64 {
	read := func() ([]uint64, error) {
		b, e := os.ReadFile("/proc/stat")
		if e != nil { return nil, e }
		f := strings.Fields(strings.SplitN(string(b), "\n", 2)[0])[1:]
		vals := make([]uint64, len(f))
		for i, s := range f { v, _ := strconv.ParseUint(s, 10, 64); vals[i] = v }
		return vals, nil
	}
	v1, e := read(); if e != nil { return nil }
	time.Sleep(200 * time.Millisecond)
	v2, _ := read()
	idle := float64(v2[3]-v1[3])
	tot := 0.0
	for i := range v2 { tot += float64(v2[i]-v1[i]) }
	if tot <= 0 { return nil }
	p := (1 - idle/tot) * 100
	return &p
}

func battery() map[string]any {
	out, e := exec.Command("termux-battery-status").Output()
	if e != nil { return map[string]any{"error": e.Error()} }
	var m map[string]any; json.Unmarshal(out, &m)
	return m
}

func main() {
	http.HandleFunc("/metrics", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		if r.Method == "OPTIONS" { w.WriteHeader(204); return }
		json.NewEncoder(w).Encode(map[string]any{
			"cpu_usage_pct": cpuPct(),
			"battery":       battery(),
		})
	})
	http.ListenAndServe(":8787", nil)
}
