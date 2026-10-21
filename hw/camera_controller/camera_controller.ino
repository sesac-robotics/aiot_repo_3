// Camera Controller - Arduino Uno firmware
// 팬틸트 서보 2개(D9, D10)와 RGB LED(D3, D5, D6)를 PC 명령대로 출력한다. 판단은 하지 않는다.
// PC 쪽 판단 로직: service/jarvis_control_service/hub/pantilt (Jarvis Control Service 내부)
// 프로토콜: USB 시리얼 9600bps, ASCII 한 줄(\n 종료, \r 무시), 각도는 정수(°).
//   PC -> Uno : C,seq,pMin,pMax,tMin,tMax,homePan,homeTilt / S,seq,pan,tilt / T,seq,pan,tilt
//               H,seq / X,seq / L,seq,R|G|B|O / B(하트비트)
//   Uno -> PC : R / K,seq[,pan,tilt] / D,seq,pan,tilt / E,seq,code
// 오류 코드: 1 형식, 2 범위 초과, 3 설정·서보 시작 전, 4 길이 초과, 5 알 수 없는 명령, 9 통신 시간 초과
#include <Servo.h>
#include <string.h>

// ---- 하드웨어 ----
const byte PAN_PIN = 9, TILT_PIN = 10;    // Servo 라이브러리가 Timer1 사용 -> D9·D10 analogWrite 금지
const byte R_PIN = 3, G_PIN = 5, B_PIN = 6;
const bool COMMON_ANODE = false;           // 공통 애노드 LED면 true

// ---- 펌웨어 고정 한계 (실물 확인값) ----
// hub/pantilt/preset_store.py의 FW_LIMITS와 같아야 한다.
// Pan 0~180: 전 구간 구동 확인. Tilt 140 초과: Pan 서보 전선과 간섭해 떨림. Tilt 30: 운용상 하한.
const int FW_PAN_MIN = 0, FW_PAN_MAX = 180;
const int FW_TILT_MIN = 30, FW_TILT_MAX = 140;
const int PULSE_MIN_US = 500, PULSE_MAX_US = 2500;   // 기존 각도 매핑(바꾸면 프리셋 재교정)

// ---- 시간 상수 ----
const unsigned long PRESET_STEP_MS = 20;   // S·H·X: 20ms마다 1° (명령 기준 약 50°/s)
const unsigned long TRACK_STEP_MS = 40;    // T: 40ms마다 1° (명령 기준 약 25°/s)
const unsigned long WATCHDOG_MS = 3000;    // 정상 명령·하트비트 무수신 허용 시간
const unsigned long BLINK_MS = 500;        // 통신 오류 깜빡임 주기

Servo pan, tilt;
int pMin = FW_PAN_MIN, pMax = FW_PAN_MAX, tMin = FW_TILT_MIN, tMax = FW_TILT_MAX;
int homePan = 81, homeTilt = 90;
int curPan = 81, curTilt = 90, tgtPan = 81, tgtTilt = 90;   // 출력 명령 각도(실측값 아님)
bool configured = false, servoStarted = false, timedOut = false;
bool needDone = false, blinkOn = false;
char moveMode = 'S';
int activeSeq = 0;
unsigned long lastRxMs = 0, lastMoveMs = 0, lastBlinkMs = 0;

char lineBuffer[64];   // 줄바꿈 제외 최대 63바이트
byte used = 0;
bool overflowed = false, badChar = false;

// 최근 4개 명령: 같은 seq·같은 내용 재수신 시 동작을 다시 시작하지 않고 응답만 재생한다.
struct Cache {
  bool valid;
  int seq;
  char raw[64];
  char ack[24];
  char done[24];
};
Cache cache[4];
byte cacheNext = 0;

void led(char color) {
  bool r = color == 'R', g = color == 'G', b = color == 'B';
  digitalWrite(R_PIN, (r != COMMON_ANODE) ? HIGH : LOW);
  digitalWrite(G_PIN, (g != COMMON_ANODE) ? HIGH : LOW);
  digitalWrite(B_PIN, (b != COMMON_ANODE) ? HIGH : LOW);
}

void errorReply(int seq, int code) {
  Serial.print(F("E,")); Serial.print(seq); Serial.print(','); Serial.println(code);
}

void clearCache() {
  for (byte i = 0; i < 4; ++i) cache[i].valid = false;
}

// 부호·공백·소수점 없는 0~32767 정수만 허용한다. 음수 각도는 쓰지 않는다.
bool integer(const char *s, int &value) {
  if (!s || !*s) return false;
  long v = 0;
  for (const char *p = s; *p; ++p) {
    if (*p < '0' || *p > '9') return false;
    v = v * 10 + (*p - '0');
    if (v > 32767) return false;
  }
  value = (int)v;
  return true;
}

bool knownOp(char op) {
  return op == 'C' || op == 'S' || op == 'T' || op == 'H' || op == 'X' || op == 'L';
}

void finishMove() {
  char reply[24];
  snprintf(reply, sizeof(reply), "D,%d,%d,%d", activeSeq, curPan, curTilt);
  Serial.println(reply);
  for (byte i = 0; i < 4; ++i) {
    if (cache[i].valid && cache[i].seq == activeSeq) strcpy(cache[i].done, reply);
  }
  needDone = false;
}

void processLine(char *raw) {
  if (strcmp(raw, "B") == 0) { lastRxMs = millis(); return; }

  char copy[64];
  strcpy(copy, raw);
  char *fields[9];
  byte n = 1;
  fields[0] = copy;
  // strtok는 빈 필드를 건너뛰므로 직접 나누어 빈 항목도 오류로 검출한다.
  for (char *p = copy; *p; ++p) {
    if (*p == ',') {
      *p = '\0';
      if (n >= 9) { errorReply(0, 1); return; }
      fields[n++] = p + 1;
    }
  }

  // 명령 문자부터 확인해, seq가 없는 줄도 알 수 없는 명령(5)과 형식 오류(1)를 구분한다.
  bool opOk = strlen(fields[0]) == 1 && knownOp(fields[0][0]);
  int seq = 0;
  if (n < 2 || !integer(fields[1], seq) || seq < 1 || seq > 999) {
    errorReply(0, opOk ? 1 : 5); return;
  }
  if (!opOk) { errorReply(seq, 5); return; }
  char op = fields[0][0];

  for (byte i = 0; i < 4; ++i) {
    if (cache[i].valid && cache[i].seq == seq) {
      if (strcmp(cache[i].raw, raw) != 0) { errorReply(seq, 1); return; }
      lastRxMs = millis();
      Serial.println(cache[i].ack);
      if (cache[i].done[0]) Serial.println(cache[i].done);
      return;
    }
  }

  byte expected = 0;
  switch (op) {
    case 'C': expected = 8; break;
    case 'S': case 'T': expected = 4; break;
    case 'H': case 'X': expected = 2; break;
    case 'L': expected = 3; break;
  }
  if (n != expected) { errorReply(seq, 1); return; }
  int v[6];
  if (op != 'L') {
    for (byte i = 2; i < n; ++i) {
      if (!integer(fields[i], v[i - 2])) { errorReply(seq, 1); return; }
    }
  }

  if (op == 'C') {
    // 운전 중 범위 변경은 허용하지 않는다(재부팅 후 다시 설정).
    if (servoStarted) { errorReply(seq, 3); return; }
    if (!(FW_PAN_MIN <= v[0] && v[0] <= v[4] && v[4] <= v[1] && v[1] <= FW_PAN_MAX &&
          FW_TILT_MIN <= v[2] && v[2] <= v[5] && v[5] <= v[3] && v[3] <= FW_TILT_MAX)) {
      errorReply(seq, 2); return;
    }
    pMin = v[0]; pMax = v[1]; tMin = v[2]; tMax = v[3];
    homePan = v[4]; homeTilt = v[5];
    configured = true;
  } else {
    if (!configured || ((op == 'S' || op == 'T' || op == 'X') && !servoStarted)) {
      errorReply(seq, 3); return;
    }
    if (op == 'L') {
      if (strlen(fields[2]) != 1 || !strchr("RBGO", fields[2][0])) {
        errorReply(seq, 1); return;
      }
      // 통신 오류 깜빡임은 새 이동 명령을 받을 때까지 유지한다.
      if (!timedOut) led(fields[2][0]);
    } else {
      int goalPan = curPan, goalTilt = curTilt;           // X: 현재 출력 각도에서 정지
      if (op == 'H') { goalPan = homePan; goalTilt = homeTilt; }
      if (op == 'S' || op == 'T') { goalPan = v[0]; goalTilt = v[1]; }
      if (goalPan < pMin || goalPan > pMax || goalTilt < tMin || goalTilt > tMax) {
        errorReply(seq, 2); return;
      }
      if (!servoStarted) {
        // 최초 H에서 서보 출력 시작. attach 직후 HOME을 지정한다(attach 전 write 금지).
        pan.attach(PAN_PIN, PULSE_MIN_US, PULSE_MAX_US); pan.write(homePan);
        tilt.attach(TILT_PIN, PULSE_MIN_US, PULSE_MAX_US); tilt.write(homeTilt);
        curPan = tgtPan = homePan; curTilt = tgtTilt = homeTilt;
        servoStarted = true;
      }
      if (timedOut) led('O');   // 깜빡임 해제. 이벤트 색은 PC가 L로 다시 설정한다.
      timedOut = false;
      // 반복되는 T로 타이머를 매번 초기화하면 이동이 멈추므로, 모드 변경·정지 상태에서만 초기화한다.
      if (moveMode != op || (curPan == tgtPan && curTilt == tgtTilt)) lastMoveMs = millis();
      tgtPan = goalPan; tgtTilt = goalTilt;
      moveMode = op; activeSeq = seq;
      needDone = op == 'S' || op == 'H';
    }
  }

  lastRxMs = millis();
  Cache &c = cache[cacheNext];
  cacheNext = (cacheNext + 1) % 4;
  c.valid = true; c.seq = seq; strcpy(c.raw, raw); c.done[0] = '\0';
  if (op == 'S' || op == 'T' || op == 'H' || op == 'X')
    snprintf(c.ack, sizeof(c.ack), "K,%d,%d,%d", seq, curPan, curTilt);
  else
    snprintf(c.ack, sizeof(c.ack), "K,%d", seq);
  Serial.println(c.ack);
  if (needDone && curPan == tgtPan && curTilt == tgtTilt) finishMove();   // 이미 목표: K 다음 D
}

void readSerial() {
  byte budget = 32;   // 한 루프에서 너무 오래 머물지 않도록 제한
  while (Serial.available() && budget--) {
    char ch = (char)Serial.read();
    if (ch == '\r') continue;
    if (ch == '\n') {
      if (overflowed) errorReply(0, 4);
      else if (badChar) errorReply(0, 1);
      else if (used) { lineBuffer[used] = '\0'; processLine(lineBuffer); }
      used = 0; overflowed = false; badChar = false;
    } else if (!overflowed && !badChar) {
      if (ch < 32 || ch > 126) badChar = true;          // 제어 문자 등은 형식 오류
      else if (used >= 63) overflowed = true;           // 나머지는 줄바꿈까지 버림
      else lineBuffer[used++] = ch;
    }
  }
}

void updateServo(unsigned long now) {
  unsigned long interval = moveMode == 'T' ? TRACK_STEP_MS : PRESET_STEP_MS;
  if (!servoStarted || now - lastMoveMs < interval) return;
  lastMoveMs = now;   // 밀린 이동을 몰아서 내지 않는다
  if (curPan == tgtPan && curTilt == tgtTilt) return;   // 목표 유지 중에는 다시 쓰지 않음
  if (curPan < tgtPan) ++curPan; else if (curPan > tgtPan) --curPan;
  if (curTilt < tgtTilt) ++curTilt; else if (curTilt > tgtTilt) --curTilt;
  pan.write(curPan); tilt.write(curTilt);
  if (needDone && curPan == tgtPan && curTilt == tgtTilt) finishMove();
}

void watchCommunication(unsigned long now) {
  if (configured && !timedOut && now - lastRxMs >= WATCHDOG_MS) {
    tgtPan = curPan; tgtTilt = curTilt; needDone = false;   // 현재 출력 각도에서 정지, 신호는 유지
    timedOut = true; blinkOn = true; lastBlinkMs = now;
    clearCache(); led('R'); errorReply(0, 9);
  }
  if (timedOut && now - lastBlinkMs >= BLINK_MS) {
    lastBlinkMs = now; blinkOn = !blinkOn; led(blinkOn ? 'R' : 'O');
  }
}

void setup() {
  Serial.begin(9600);
  pinMode(R_PIN, OUTPUT); pinMode(G_PIN, OUTPUT); pinMode(B_PIN, OUTPUT);
  led('O');
  clearCache();
  lastRxMs = millis();
  Serial.println(F("R"));   // 서보 출력은 C 설정 후 최초 H에서 시작
}

void loop() {
  readSerial();
  unsigned long now = millis();
  watchCommunication(now);
  updateServo(now);
}
