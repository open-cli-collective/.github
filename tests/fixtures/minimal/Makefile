.PHONY: build test test-cover lint

build:
	go build ./...

test:
	go test ./...

test-cover:
	go test -coverprofile=coverage.out ./...

lint:
	golangci-lint run
