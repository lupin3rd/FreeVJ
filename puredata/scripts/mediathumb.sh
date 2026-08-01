#!/bin/bash
#echo "Arg 1: $1"
#echo "Arg 2: $2"
input=$1
filename=$2
position=$3
decoded=$(printf '%b' "${input//%/\\x}")
ffmpegthumbnailer -i "${decoded}" -o /tmp/${filename}.png -t ${position} -s 120
