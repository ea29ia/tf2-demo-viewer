#!/bin/sh
# Download a TF2 map from public fastdl servers.
# Usage: ./tools/get_map.sh <mapname>            e.g. ./tools/get_map.sh pl_badwater
#        ./tools/get_map.sh <mapname> bz2        (download .bz2 and decompress)
set -e

MAP="$1"
FORMAT="${2:-bsp}"
OUT="assets/${MAP}.bsp"

if [ -z "$MAP" ]; then
  echo "usage: $0 <mapname> [bsp|bz2]"
  exit 2
fi

mkdir -p assets

# Known fastdl mirrors (checked at runtime, first hit wins)
MIRRORS="
https://fastdl.serveme.tf/maps
https://dl.serveme.tf/maps
https://fastdl.uncletopia.com/maps
https://fastdl.skial.com/maps
https://fastdl.tf2maps.eu/maps
"

for base in $MIRRORS; do
  url="$base/$MAP.$FORMAT"
  echo "== trying $url"
  code=$(curl -sIL -A "Mozilla/5.0" -o /dev/null -w '%{http_code}' --max-time 20 "$url" || echo 000)
  if [ "$code" = "200" ]; then
    echo "== downloading"
    curl -sL -A "Mozilla/5.0" --max-time 600 -o "assets/$MAP.$FORMAT" "$url"
    break
  fi
  echo "   HTTP $code, next mirror"
done

if [ ! -s "assets/$MAP.$FORMAT" ]; then
  echo "FAILED: no mirror served $MAP.$FORMAT"
  echo "Try manual sources: tf2maps.net, gamebanana.com, or your own TF2 install"
  echo "(steamapps/common/Team\ Fortress\ 2/tf/maps/${MAP}.bsp)"
  exit 1
fi

if [ "$FORMAT" = "bz2" ]; then
  bunzip2 -f "assets/$MAP.bz2"
fi

ls -lh "$OUT"
echo "OK: $OUT"
echo "Next steps:"
echo "  python3 tools/bsp2glb.py assets/$MAP.bsp web/public/maps/$MAP.glb"
echo "  python3 tools/add_props_glb.py web/public/maps/$MAP.glb assets/$MAP.bsp"
echo "  python3 tools/add_kits_glb.py web/public/maps/$MAP.glb assets/$MAP.bsp   # if kit points needed"
echo "  python3 tools/validate_glb.py web/public/maps/$MAP.glb"
