package main

import (
    "encoding/json"
    "io/ioutil"
    "net/http"
    "os/exec"
    "strconv"
    "strings"
)

type Metrics struct {
    CPUUsage float64            `json:"cpu_usage_pct"`
    Battery  map[string]any     `json:"battery"`
}

func readCPU() float64 {
    data, _ := ioutil.ReadFile("/proc/stat")
    fields := strings.Fields(strings.Split(string(data), "\n")[0])
    // fields[0] = "cpu"
    var vals []int64
    for _, f := range fields[1:8] {
        v, _ := strconv.ParseInt(f, 10, 64)
        vals = append(vals, v)
    }
    user,nice,system,idle,iowait,irq,softirq := vals[0],vals[1],vals[2],vals[3],vals[4],vals[5],vals[6]
    idleAll := idle + iowait
    nonIdle := user + nice + system + irq + softirq
    total := idleAll + nonIdle
    return float64(nonIdle) / float64(total) * 100.0
}

func readBattery() map[string]any {
    out, err := exec.Command("termux-battery-status").Output()
    if err != nil {
        return map[string]any{"error": err.Error()}
    }
    var m map[string]any
    json.Unmarshal(out, &m)
    return m
}

func main() {
    http.HandleFunc("/metrics", func(w http.ResponseWriter, r *http.Request) {
        metrics := Metrics{
            CPUUsage: readCPU(),
            Battery:  readBattery(),
        }
        json.NewEncoder(w).Encode(metrics)
    })
    http.ListenAndServe(":8787", nil)
}
