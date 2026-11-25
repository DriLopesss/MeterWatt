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
  std::deque<char> entrada;          // o que a pessoa digitou no Monitor Serial
  std::string saida;                 // tudo o que o firmware escreveu
  void begin(int) {}
  void digitar(const std::string& s) { for (char c : s) entrada.push_back(c); }
  int available() { return (int)entrada.size(); }
  int read() { char c = entrada.front(); entrada.pop_front(); return (uint8_t)c; }
  void printf(const char* f, ...) {
    char b[512]; va_list a; va_start(a, f); vsnprintf(b, sizeof(b), f, a); va_end(a); saida += b; std::printf("%s", b);
  }
  void println(const char* s) { saida += s; saida += "\n"; std::printf("%s\n", s); }
} Serial;
enum ModoPzem { NORMAL, MUDO, CORROMPIDO };
// Um PZEM de mentira. Vários podem dividir o mesmo par de fios, cada um com o seu endereço.
struct ModuloPzem {
  uint8_t endereco = 0x01;           // de fábrica
  ModoPzem modo = NORMAL; uint32_t energia = 1234567; bool zerou = false;
  uint16_t tensao = 1273; uint32_t corrente = 4321, potencia = 5482; uint16_t fp = 98;
};
struct HardwareSerial {
  std::deque<uint8_t> fila; int baud = 0, rx = -1, tx = -1;
  // Espaço para 4 módulos desde o início: os atalhos abaixo apontam para o primeiro e não podem mudar de lugar.
  static std::vector<ModuloPzem> criar() { std::vector<ModuloPzem> v; v.reserve(4); v.push_back(ModuloPzem()); return v; }
  std::vector<ModuloPzem> modulos = criar();
  // Atalhos para o caso de um módulo só (os testes antigos).
  ModoPzem& modo = modulos[0].modo; bool& zerou = modulos[0].zerou;
  HardwareSerial() {}
  HardwareSerial(const HardwareSerial&) = delete;
  void begin(int b, int, int r, int t) { baud = b; rx = r; tx = t; }
  int available() { return (int)fila.size(); }
  int read() { uint8_t b = fila.front(); fila.pop_front(); return b; }
  void flush() {}
  void responder(ModuloPzem& m, const uint8_t* p, size_t n, std::vector<uint8_t>& saida) {
    if (m.modo == MUDO) return;
    if (n == 8 && p[1] == 0x04) {
      uint8_t r[25] = {m.endereco, 0x04, 20};
      auto w = [&](int pos, uint16_t x) { r[pos] = x >> 8; r[pos + 1] = x & 0xFF; };
      w(3, m.tensao); w(5, m.corrente & 0xFFFF); w(7, m.corrente >> 16); w(9, m.potencia & 0xFFFF); w(11, m.potencia >> 16);
      w(13, m.energia & 0xFFFF); w(15, m.energia >> 16); w(17, 600); w(19, m.fp); w(21, 0);
      uint16_t c = pzem::crc16(r, 23); r[23] = c & 0xFF; r[24] = c >> 8;
      if (m.modo == CORROMPIDO) r[10] ^= 0x40;
      saida.insert(saida.end(), r, r + 25);
      m.energia += 1;
    } else if (n == 4 && p[1] == 0x42) {
      saida.insert(saida.end(), p, p + 4); m.energia = 0; m.zerou = true;
    } else if (n == 8 && p[1] == 0x06 && p[2] == 0x00 && p[3] == 0x02 && pzem::enderecoValido(p[5])) {
      saida.insert(saida.end(), p, p + 8); m.endereco = p[5];
    }
  }
  size_t write(const uint8_t* p, size_t n) {
    if (!pzem::crcConfere(p, n)) return n;
    // Todos os módulos ouvem; responde quem tem o endereço (o geral, 0xF8, faz TODOS responderem).
    std::vector<std::vector<uint8_t>> respostas;
    for (auto& m : modulos) {
      if (p[0] != m.endereco && p[0] != pzem::ENDERECO_GERAL) continue;
      std::vector<uint8_t> r; responder(m, p, n, r);
      if (!r.empty()) respostas.push_back(r);
    }
    if (respostas.size() == 1) for (uint8_t b : respostas[0]) fila.push_back(b);
    else if (respostas.size() > 1) for (size_t i = 0; i < respostas[0].size(); i++) fila.push_back(respostas[0][i] | respostas[1][i]);  // colisão
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
// `nvs` são os ajustes (área "meterwatt"); `nvsId` é a identidade do medidor (área "mw-id")
static std::map<std::string, std::string> nvs, nvsId;
struct Preferences {
  bool soLeitura = true; std::map<std::string, std::string>* m = &nvs;
  bool begin(const char* area, bool ro) { soLeitura = ro; m = std::string(area) == "meterwatt" ? &nvs : &nvsId; return true; } void end() {}
  bool isKey(const char* k) { return m->count(k) > 0; }
  size_t getString(const char* k, char* v, size_t max) { if (!m->count(k)) return 0; std::strncpy(v, (*m)[k].c_str(), max - 1); v[max - 1] = 0; return std::strlen(v); }
  uint16_t getUShort(const char* k, uint16_t padrao) { return m->count(k) ? (uint16_t)std::stoi((*m)[k]) : padrao; }
  bool getBool(const char* k, bool padrao) { return m->count(k) ? (*m)[k] == "1" : padrao; }
  size_t putString(const char* k, const char* v) { if (soLeitura) { std::printf("ERRO: gravou em modo leitura\n"); return 0; } (*m)[k] = v; return 1; }
  size_t putUShort(const char* k, uint16_t v) { if (soLeitura) return 0; (*m)[k] = std::to_string(v); return 2; }
  size_t putBool(const char* k, bool v) { if (soLeitura) { std::printf("ERRO: gravou em modo leitura\n"); return 0; } (*m)[k] = v ? "1" : "0"; return 1; }
  bool clear() { m->clear(); return true; }
};
static uint32_t sementeAleatoria = 0x1000;
inline uint32_t esp_random() { return sementeAleatoria += 0x01234567; }
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
struct Client {};
struct WiFiClient : Client {};
struct WiFiClientSecure : Client { const char* ca = nullptr; void setCACert(const char* c) { ca = c; } };
struct Publicacao { std::string topico, corpo; bool retido; };
static std::vector<Publicacao> publicadas;
static std::vector<std::string> assinados;
static int recusarLogin = 0;        // 0 = aceita; senão, o estado MQTT devolvido para a senha própria (4 ou 5)
struct PubSubClient {
  bool conectado = false; void (*cb)(char*, byte*, unsigned int) = nullptr; std::string lwt; size_t buf = 256;
  Client* cliente = nullptr; std::string usuario, senha, idCliente; int estado = 0, tentativas = 0;
  PubSubClient(Client& c) : cliente(&c) {}
  void setClient(Client& c) { cliente = &c; }
  void setServer(const char*, int) {} void setBufferSize(size_t n) { buf = n; }
  void setCallback(void (*f)(char*, byte*, unsigned int)) { cb = f; }
  bool connect(const char* id, const char* u, const char* s, const char* t, int, bool, const char*) {
    tentativas++; usuario = u; senha = s; lwt = t; idCliente = id;
    if (recusarLogin && std::string(id).rfind(usuario + "-", 0) == 0) { estado = recusarLogin; conectado = false; return false; }
    estado = 0; conectado = true; return true; }
  bool connected() { return conectado; } int state() { return estado; } void loop() {} void disconnect() { conectado = false; desconexoes++; } int desconexoes = 0;
  bool subscribe(const char* t) { assinados.push_back(t); return true; }
  bool publish(const char* t, const char* p, bool r = false) {
    if (strlen(t) + strlen(p) + 5 > buf) { std::printf("ERRO: mensagem maior que o buffer MQTT\n"); return false; }
    publicadas.push_back({t, p, r}); return true; }
};
