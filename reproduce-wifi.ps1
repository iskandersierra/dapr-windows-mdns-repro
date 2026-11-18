param(
    [string] $Interface = 'Wi-Fi',
    [ValidateRange(1, 20)] [int] $Restarts = 3
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$env:CGO_ENABLED = '0'
$env:GOTOOLCHAIN = 'local'
if ((go env GOVERSION GOOS GOARCH) -join ' ' -ne 'go1.26.4 windows amd64') {
    throw 'This experiment requires Go 1.26.4 on Windows amd64.'
}

$root = Join-Path $PSScriptRoot ('wifi-test-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$source = Join-Path $root 'source'
New-Item -ItemType Directory -Path $root | Out-Null
Write-Output "Logs and builds: $root"
git clone --quiet --depth 1 --branch v1.18.0 https://github.com/dapr/dapr.git $source
Push-Location $source

try {
    $versions = @('v1.0.0', 'v1.0.1-0.20230119201135-e4f60f8407b1')
    $names = @('control', 'patched')
    $binaries = foreach ($i in 0, 1) {
        $version = $versions[$i]
        $name = $names[$i]
        $module = go mod download -json "github.com/grandcat/zeroconf@$version" | ConvertFrom-Json
        $local = Join-Path $root "zeroconf-$name"
        Copy-Item -LiteralPath $module.Dir -Destination $local -Recurse
        $connection = Get-Item -LiteralPath "$local/connection.go"
        $connection.IsReadOnly = $false
        $text = Get-Content -Raw -LiteralPath $connection.FullName
        $marker = 'func listMulticastInterfaces() []net.Interface {'
        if (!$text.Contains($marker)) { throw "Missing interface enumeration in zeroconf $version." }
        $selection = @'
func listMulticastInterfaces() []net.Interface {
    if name := os.Getenv("DAPR_REPRO_MDNS_INTERFACE"); name != "" {
        iface, err := net.InterfaceByName(name)
        if err != nil || iface.Flags&net.FlagUp == 0 || iface.Flags&net.FlagMulticast == 0 {
            panic("repro interface unavailable or multicast disabled")
        }
        println("repro mDNS interface:", name)
        return []net.Interface{*iface}
    }
'@
        $text = $text.Replace('"net"', "`"net`"`n`t`"os`"").Replace($marker, $selection)
        [IO.File]::WriteAllText($connection.FullName, $text)
        gofmt -w $connection.FullName

        Copy-Item -LiteralPath go.mod -Destination "$name.mod"
        Copy-Item -LiteralPath go.sum -Destination "$name.sum"
        go mod edit "-modfile=$name.mod" "-require=github.com/grandcat/zeroconf@$version" "-replace=github.com/grandcat/zeroconf=$local"
        $binary = Join-Path $root "daprd-$name.exe"
        $flags = '-X github.com/dapr/dapr/pkg/buildinfo.version=1.18.0 -X github.com/dapr/kit/logger.DaprVersion=1.18.0 -s -w'
        go build "-modfile=$name.mod" '-mod=mod' '-tags=allcomponents' "-ldflags=$flags" -o $binary './cmd/daprd'
        go version -m $binary | Set-Content -LiteralPath "$root/$name-build.txt"
        $binary
    }

    $env:DAPR_REPRO_MDNS_INTERFACE = $Interface
    python "$PSScriptRoot/repro.py" $binaries[0] $binaries[1] $binaries[0] --app-ids repro-single --restarts $Restarts --output "$root/requests"
} finally {
    Pop-Location
}
