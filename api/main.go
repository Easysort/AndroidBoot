package main

import (
	"encoding/json"
	"net/http"
	"os/exec"
)

func battery() map[string]any {
	out, err := exec.Command("termux-battery-status").Output()
	if err != nil { return map[string]any{"error": err.Error()} }
	var m map[string]any; json.Unmarshal(out, &m)
	return m
}

func main() {
	http.HandleFunc("/metrics", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		if r.Method=="OPTIONS"{w.WriteHeader(204);return}
		json.NewEncoder(w).Encode(battery())
	})
	http.ListenAndServe(":8787", nil)
}
