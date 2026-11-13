// ESP32 de mentira, só para rodar o firmware de verdade no computador.
// Imita o mínimo de cada biblioteca (Wi-Fi, MQTT, memória, página de configuração,
// atualização pelo Wi-Fi) e um PZEM que responde pelo protocolo Modbus.
// Não substitui o teste com a placa: serve para pegar erros de lógica antes dele.
#pragma once
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <string>
#include <vector>
#include <cmath>
#include "../../../firmware/mwatt_pzem/pzem_modbus.h"
#include <map>
#include <functional>
typedef uint8_t byte;
static unsigned long relogio_ms = 0;
inline unsigned long millis() { return relogio_ms; }
inline void delay(unsigned long ms) { relogio_ms += ms; }
#define SERIAL_8N1 0
#define WL_CONNECTED 3
#define WIFI_STA 1
struct SerialUsb {
  void begin(int) {}
  void printf(const char* f, ...) { va_list a; va_start(a, f); vprintf(f, a); va_end(a); }
  void println(const char* s) { std::printf("%s\n", s); }
} Serial;
enum ModoPzem { NORMAL, MUDO, CORROMPIDO };
struct HardwareSerial {
  std::deque<uint8_t> fila; ModoPzem modo = NORMAL; uint32_t energia = 1234567; int baud = 0, rx = -1, tx = -1; bool zerou = false;
  void begin(int b, int, int r, int t) { baud = b; rx = r; tx = t; }
  int available() { return (int)fila.size(); }
  int read() { uint8_t b = fila.front(); fila.pop_front(); return b; }
  void flush() {}
  size_t write(const uint8_t* p, size_t n) {
    if (modo == MUDO || !pzem::crcConfere(p, n)) return n;
    if (n == 8 && p[1] == 0x04) {
      uint8_t r[25] = {0x01, 0x04, 20};
      auto w = [&](int pos, uint16_t x) { r[pos] = x >> 8; r[pos + 1] = x & 0xFF; };
      w(3, 1273); w(5, 4321); w(7, 0); w(9, 5482); w(11, 0);
      w(13, energia & 0xFFFF); w(15, energia >> 16); w(17, 600); w(19, 98); w(21, 0);
      uint16_t c = pzem::crc16(r, 23); r[23] = c & 0xFF; r[24] = c >> 8;
      if (modo == CORROMPIDO) r[10] ^= 0x40;
      for (uint8_t b : r) fila.push_back(b);
      energia += 1;
    } else if (n == 4 && p[1] == 0x42) { for (size_t i = 0; i < 4; i++) fila.push_back(p[i]); energia = 0; zerou = true; }
    return n;
  }
} Serial2;
struct IPFalso { std::string toString() const { return "192.168.0.50"; } };
static bool wifiConectado = true;
struct WiFiFalso {
  int status() { return wifiConectado ? WL_CONNECTED : 0; } void mode(int) {} void setAutoReconnect(bool) {}
  void begin(const char*, const char*) {} int RSSI() { return -61; } IPFalso localIP() { return IPFalso(); }
} WiFi;
static int reinicios = 0;
struct EspFalso { uint64_t getEfuseMac() { return 0x0000A1B2C3D4E5F6ULL; } void restart() { reinicios++; } } ESP;
#define INPUT_PULLUP 2
#define LOW 0
#define HIGH 1
static int nivelBotao = HIGH;
inline void pinMode(int, int) {}
inline int digitalRead(int) { return nivelBotao; }
// memória do ESP32 (Preferences)
static std::map<std::string, std::string> nvs;
struct Preferences {
  bool soLeitura = true;
  bool begin(const char*, bool ro) { soLeitura = ro; return true; } void end() {}
  bool isKey(const char* k) { return nvs.count(k) > 0; }
  size_t getString(const char* k, char* v, size_t max) { if (!nvs.count(k)) return 0; std::strncpy(v, nvs[k].c_str(), max - 1); v[max - 1] = 0; return std::strlen(v); }
  uint16_t getUShort(const char* k, uint16_t padrao) { return nvs.count(k) ? (uint16_t)std::stoi(nvs[k]) : padrao; }
  size_t putString(const char* k, const char* v) { if (soLeitura) { std::printf("ERRO: gravou em modo leitura\n"); return 0; } nvs[k] = v; return 1; }
  size_t putUShort(const char* k, uint16_t v) { if (soLeitura) return 0; nvs[k] = std::to_string(v); return 2; }
  bool clear() { nvs.clear(); return true; }
};
// atualização pelo Wi-Fi
typedef int ota_error_t;
struct OtaFalso {
  std::string host, senha; int comecou = 0, chamadas = 0;
  void setHostname(const char* h) { host = h; } void setPassword(const char* s) { senha = s; }
  void onStart(std::function<void()>) {} void onEnd(std::function<void()>) {} void onError(std::function<void(ota_error_t)>) {}
  void begin() { comecou++; } void handle() { chamadas++; }
} ArduinoOTA;
// página de configuração
struct WiFiManagerParameter {
  std::string id, valor;
  WiFiManagerParameter(const char* i, const char*, const char* v, int, const char* = "") : id(i), valor(v) {}
  void setValue(const char* v, int) { valor = v; } const char* getValue() { return valor.c_str(); }
};
struct WiFiManager {
  std::vector<WiFiManagerParameter*> campos; void (*salvar)() = nullptr; std::string rede, senhaRede; int resets = 0, processos = 0;
  void addParameter(WiFiManagerParameter* p) { campos.push_back(p); }
  void setSaveParamsCallback(void (*f)()) { salvar = f; } void setTitle(const char*) {} void setConfigPortalBlocking(bool) {} void setConnectTimeout(int) {}
  bool autoConnect(const char* r, const char* s) { rede = r; senhaRede = s; return wifiConectado; }
  void process() { processos++; } void resetSettings() { resets++; }
  bool portalAberto = false; int aberturas = 0, tempoLimite = 0;
  bool getConfigPortalActive() { return portalAberto; } void setConfigPortalTimeout(int s) { tempoLimite = s; }
  bool startConfigPortal(const char* r, const char* s) { rede = r; senhaRede = s; portalAberto = true; aberturas++; return false; }
  // o celular envia o formulário
  void enviar(std::map<std::string, std::string> v) { for (auto* c : campos) if (v.count(c->id)) c->valor = v[c->id]; if (salvar) salvar(); }
};
struct WiFiClient {};
struct Publicacao { std::string topico, corpo; bool retido; };
static std::vector<Publicacao> publicadas;
static std::vector<std::string> assinados;
struct PubSubClient {
  bool conectado = false; void (*cb)(char*, byte*, unsigned int) = nullptr; std::string lwt; size_t buf = 256;
  PubSubClient(WiFiClient&) {}
  void setServer(const char*, int) {} void setBufferSize(size_t n) { buf = n; }
  void setCallback(void (*f)(char*, byte*, unsigned int)) { cb = f; }
  bool connect(const char*, const char*, const char*, const char* t, int, bool, const char*) { lwt = t; conectado = true; return true; }
  bool connected() { return conectado; } int state() { return 0; } void loop() {} void disconnect() { conectado = false; desconexoes++; } int desconexoes = 0;
  bool subscribe(const char* t) { assinados.push_back(t); return true; }
  bool publish(const char* t, const char* p, bool r = false) {
    if (strlen(t) + strlen(p) + 5 > buf) { std::printf("ERRO: mensagem maior que o buffer MQTT\n"); return false; }
    publicadas.push_back({t, p, r}); return true; }
};
