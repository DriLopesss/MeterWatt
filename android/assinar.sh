#!/usr/bin/env bash
# Assina o que o Gradle compilou em android/projeto:
#   - meterwatt.aab: o pacote que vai para a Play Store (assinado com a chave de envio)
#   - meterwatt.apk: o mesmo app para instalar direto no celular, sem a Play (testes)
#
#   ANDROID_CHAVE_SENHA=... android/assinar.sh <arquivo .keystore> <apelido da chave> <pasta de saída>
#
# A senha vem só da variável de ambiente: na linha de comando ela apareceria nos registros.
# Precisa do JDK (keytool, jarsigner) e das build-tools do Android (zipalign, apksigner) em $ANDROID_HOME.
set -euo pipefail

chave="$1"
apelido="$2"
saida="$3"
: "${ANDROID_CHAVE_SENHA:?Defina ANDROID_CHAVE_SENHA com a senha da chave.}"
compilado="$(cd "$(dirname "$0")" && pwd)/projeto/app/build/outputs"
ferramentas="$(ls -d "${ANDROID_HOME:?Defina ANDROID_HOME (a pasta do Android SDK).}"/build-tools/* | sort -V | tail -n 1)"
mkdir -p "$saida"

# .aab: assinatura de JAR, que é o que a Play Console espera de quem envia.
# (Na chave PKCS12, a senha da chave é a mesma do arquivo.)
jarsigner -keystore "$chave" -storepass:env ANDROID_CHAVE_SENHA -keypass:env ANDROID_CHAVE_SENHA \
  -sigalg SHA256withRSA -digestalg SHA-256 \
  -signedjar "$saida/meterwatt.aab" "$compilado/bundle/release/app-release.aab" "$apelido"
if ! jarsigner -verify "$saida/meterwatt.aab" | grep -q "jar verified"; then
  echo "A assinatura do .aab não conferiu." >&2
  exit 1
fi

# .apk: alinhar antes de assinar (o apksigner usa os esquemas v2 e v3, que o zipalign quebraria depois).
"$ferramentas/zipalign" -f -p 4 "$compilado/apk/release/app-release-unsigned.apk" "$saida/alinhado.apk"
"$ferramentas/apksigner" sign --ks "$chave" --ks-key-alias "$apelido" \
  --ks-pass env:ANDROID_CHAVE_SENHA --key-pass env:ANDROID_CHAVE_SENHA \
  --out "$saida/meterwatt.apk" "$saida/alinhado.apk"
rm -f "$saida/alinhado.apk" "$saida/meterwatt.apk.idsig"
"$ferramentas/apksigner" verify --print-certs "$saida/meterwatt.apk" | grep -i "SHA-256"

# A impressão SHA-256 desta chave, no formato do MW_ANDROID_SHA256 do servidor.
keytool -list -v -keystore "$chave" -storepass:env ANDROID_CHAVE_SENHA -alias "$apelido" \
  | sed -n 's/^[[:space:]]*SHA256:[[:space:]]*//p' > "$saida/impressao-sha256.txt"
echo "Impressão SHA-256 da chave: $(cat "$saida/impressao-sha256.txt")"
