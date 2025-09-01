# AndroidBoot

Tiny API on Android to get valuable information when used as production products.

The api returns: temp, battery level, charging status.

uploader.py uploads the metrics to a remote server every minute. In this case we also take a picture with the camera as a sanity check.

The Binary is built for android arm64 using `GOOS=android GOARCH=arm64 CGO_ENABLED=0 go build -o metrics-api`

Install termux to run api.go:
```
pkg update
pkg install golang termux-api
./api
```
