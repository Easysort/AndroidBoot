# AndroidBoot

Tiny API on Android to get valuable information when used as production products.

Run the api.go binary on 3 android phones and the debug view on either one of them or a fourth.

The Binary is built for android arm64 using `GOOS=android GOARCH=arm64 CGO_ENABLED=0 go build -o metrics-api`

Install termux to run api.go:
```
pkg update
pkg install golang termux-api
./api
```
