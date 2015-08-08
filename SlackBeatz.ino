
#include <LiquidCrystal.h>
#include <EEPROM.h>
#include <SoftwareSerial.h>




SoftwareSerial midiSerial(A4, A5); // digital pins that we'll use for soft serial RX & TX
LiquidCrystal lcd(8, 9, 4, 5, 6, 7);            // select the pins used on the LCD panel

// define some values used by the panel and buttons
int lcd_key     = 0;
int adc_key_in  = 0;

#define BTN_RIGHT  0
#define BTN_UP     1
#define BTN_DOWN   2
#define BTN_LEFT   3
#define BTN_SELECT 4
#define BTN_NONE   5


#define PRM_TYPE_NUM 0
#define PRM_TYPE_TOGGLE 1
#define PRM_TOGGLE_ON 1
#define PRM_TOGGLE_OFF 0

#define PARAM_EEPROM_BASE 0

enum PARAMS {
  PARAM_BPM,
  PARAM_BD_FREQ,
  PARAM_SD_FREQ,
  PARAM_HH_FREQ,
  PARAM_BD_NOTE,
  PARAM_SD_NOTE,
  PARAM_HH_NOTE,
  PARAM_BD_OFST,
  PARAM_SD_OFST,
  PARAM_HH_OFST,
  PARAM_DRUM_CH,
  PARAM_DRUM_FILL,
  PARAM_SWING,
  PARAM_EOF    //must be last in list
};

#define BEAT_CHAR_PAUSED_1 0x9A
#define BEAT_CHAR_PAUSED_2 0xA5
#define BEAT_CHAR_PLAYING 0x2A

#define STEPS 16
#define MAX_NOTE 127
#define DRUM_VELOCITY 100
#define FILL_BARS 4
#define bpm param[PARAM_BPM]


byte bd_rhythm[STEPS];
byte sd_rhythm[STEPS];
byte hh_rhythm[STEPS];

#define MENU_SIZE PARAM_EOF

const char * menu_items[MENU_SIZE];


byte param_type[MENU_SIZE];
byte param_min[MENU_SIZE];
byte param_max[MENU_SIZE];
byte param_default[MENU_SIZE];

byte param[MENU_SIZE];

char * beat_display = "________________";

signed char menu_index = 0;
bool  playing = false;
byte tick = 0;            // 24 clock ticks per quarter note
byte beat = 0;            // where in this
int bar = 0;
int last_button;
int repeat_count;

#define ADD_MENU_ITEM(__idx,__prm_type,__name,__min,__max,__default) \
  menu_items[__idx]=__name;\
  param_type[__idx]=__prm_type;\
  param_min[__idx]=__min;\
  param_max[__idx]=__max;\
  param_default[__idx]=__default;


void setup() {

  ADD_MENU_ITEM(PARAM_BPM,    PRM_TYPE_NUM, "BPM      : ", 30, 180, 120);      //beats per minute
  ADD_MENU_ITEM(PARAM_BD_FREQ, PRM_TYPE_NUM, "BD FREQ : ", 0, STEPS, 4);    //how frequently this drum hit occurs
  ADD_MENU_ITEM(PARAM_SD_FREQ, PRM_TYPE_NUM, "SD FREQ : ", 0, STEPS, 7);
  ADD_MENU_ITEM(PARAM_HH_FREQ, PRM_TYPE_NUM, "HH FREQ : ", 0, STEPS, 11);
  ADD_MENU_ITEM(PARAM_BD_NOTE, PRM_TYPE_NUM, "BD NOTE : ", 0, MAX_NOTE, 36); //MIDI note sent for 'bass drum' hits
  ADD_MENU_ITEM(PARAM_SD_NOTE, PRM_TYPE_NUM, "SD NOTE : ", 0, MAX_NOTE, 38); //MIDI note sent for 'snare drum' hits
  ADD_MENU_ITEM(PARAM_HH_NOTE, PRM_TYPE_NUM, "HH NOTE : ", 0, MAX_NOTE, 42); //MIDI note sent for 'high hat' hits
  ADD_MENU_ITEM(PARAM_BD_OFST, PRM_TYPE_NUM, "BD OFST : ", 0, STEPS - 1, 0); //offset from first step of sequence when distributing drum hits
  ADD_MENU_ITEM(PARAM_SD_OFST, PRM_TYPE_NUM, "SD OFST : ", 0, STEPS - 1, 0);
  ADD_MENU_ITEM(PARAM_HH_OFST, PRM_TYPE_NUM, "HH OFST : ", 0, STEPS - 1, 0);
  ADD_MENU_ITEM(PARAM_DRUM_CH, PRM_TYPE_NUM, "DRUM CH : ", 1, 16, 10);
  ADD_MENU_ITEM(PARAM_DRUM_FILL, PRM_TYPE_TOGGLE, "FILL    : ", 0, 1, 0);
  ADD_MENU_ITEM(PARAM_SWING, PRM_TYPE_NUM,   "SWING % : ", 30, 70, 50);

  //  Set MIDI baud rate:
  midiSerial.begin(31250);


  lcd.begin(16, 2);               // start the library
  // do an animation thing
  screen_wipe(' ', 15);
  //  screen_wipe('>', 15);
  lcd.setCursor(4, 0);
  lcd.print("sLACK");
  delay(500);
  lcd.setCursor(7, 1);
  lcd.print("bEATZ");
  delay(500);
  screen_wipe(' ', 15);

  lcd_key = read_LCD_buttons();   // read the buttons


  //  if 'SELECT' held down on startup, do a factory reset (but don't save)
  if (lcd_key == BTN_SELECT) {
    screen_wipe('*', 15);
    for (int i = 0; i < PARAM_EOF; i++) {
      param[i] = param_default[i];
    }

    //wait until 'SELECT' is released
    for (; read_LCD_buttons() == BTN_SELECT;) {}

    screen_wipe(' ', 15);
  } else { //load params from EEPROM

    for (int i = 0; i < PARAM_EOF; i++) {
      param[i] = EEPROM.read(PARAM_EEPROM_BASE + i);
      if (param[i] == 0xFF) {
        param[i] = param_default[i];
      }

      if (param[i] > param_max[i]) {
        param[i] = param_max[i];
      }

      if (param[i] < param_min[i]) {
        param[i] = param_min[i];
      }
    }
  }
  //debug output on UART
  Serial.begin(9600);

  show_menu();

}

int read_LCD_buttons() {              // read the buttons
  adc_key_in = analogRead(0);       // read the value from the sensor

  //buttons when read are centered at these values: 0, 144, 329, 504, 741
  // we add approx 50 to those values and check to see if we are close

  if (adc_key_in > 1000) return BTN_NONE;

  if (adc_key_in < 50)   return BTN_RIGHT;
  if (adc_key_in < 195)  return BTN_UP;
  if (adc_key_in < 380)  return BTN_DOWN;
  if (adc_key_in < 555)  return BTN_LEFT;
  if (adc_key_in < 790)  return BTN_SELECT;

  return BTN_NONE;                // when all others fail, return this.
}




void show_menu() {
  lcd.setCursor(0, 0);
  lcd.print(menu_items[menu_index]);
  switch (param_type[menu_index]) {
    case PRM_TYPE_NUM:
      lcd.print(param[menu_index]);
      break;
    case PRM_TYPE_TOGGLE:
      if (param[menu_index] == PRM_TOGGLE_ON) {
        lcd.print("ON");    //overwrite any remaining digits
      } else {
        lcd.print("OFF");    //overwrite any remaining digits

      }
      break;
  }
  lcd.print("  ");    //overwrite any remaining chars on this line
  lcd.setCursor(0, 1);
  lcd.print(beat_display);
}

void screen_wipe(char c, int d) {
  lcd.setCursor(0, 0);
  for (int i = 0; i < 2; i++) {
    lcd.setCursor(0, i);
    for (int j = 0; j < 16; j++) {
      lcd.print(c);
      delay (d);
    }
  }

}

void distribute_notes(byte * rhythm, byte note, int pulses, int steps, int offset) {
  int i = 0;
  for (i = 0; i < steps; i++) {
    rhythm[i] = 0;
  }
  i = offset;

  int pauses  = steps - pulses;
  if (pulses >= 0) {
    int per_pulse = (pauses / pulses);
    int remainder = pauses % pulses;

    for (int pulse = 0; pulse < pulses; pulse++) {
      rhythm[i % steps] = note;
      i += 1;
      i += per_pulse;
      if (pulse < remainder) {
        i++;
      }
    }
  }
}


void do_sequencer_step(int step_number) {
  if (bd_rhythm[step_number] > 0) {
    midiSerial.write(0x8F + param[PARAM_DRUM_CH]);    //note on
    midiSerial.write(bd_rhythm[step_number]);
    midiSerial.write(DRUM_VELOCITY);
  }

  if (hh_rhythm[step_number] > 0) {
    midiSerial.write(0x8F + param[PARAM_DRUM_CH]);    //note on
    midiSerial.write(hh_rhythm[step_number]);
    midiSerial.write(DRUM_VELOCITY);
  }

  if (sd_rhythm[step_number] > 0) {
    midiSerial.write(0x8F + param[PARAM_DRUM_CH]);    //note on
    midiSerial.write(sd_rhythm[step_number]);
    midiSerial.write(DRUM_VELOCITY);
  }

}

void loop() {


  unsigned long loopStart;
  unsigned long delayUntil;
  loopStart = micros();

  beat_display[beat] = playing ? BEAT_CHAR_PLAYING : ((tick > 11) ? BEAT_CHAR_PAUSED_1 : BEAT_CHAR_PAUSED_2);
  show_menu();
  beat_display[beat] = '_';

  lcd_key = read_LCD_buttons();   // read the buttons
  if (last_button == lcd_key) {
    repeat_count++;
  } else {
    repeat_count = 0;
  }

  //recalculate drum sequence every time - this way we won't 'stutter'
  if (param[PARAM_DRUM_FILL] && ((bar % FILL_BARS) == FILL_BARS - 1)) {
    //do a fill by swapping around drum parts

    distribute_notes(bd_rhythm, param[PARAM_BD_NOTE], STEPS-param[PARAM_BD_FREQ], STEPS, param[PARAM_BD_OFST]);
    distribute_notes(sd_rhythm, param[PARAM_HH_NOTE], param[PARAM_SD_FREQ], STEPS, param[PARAM_SD_OFST]);
    distribute_notes(hh_rhythm, param[PARAM_SD_NOTE], param[PARAM_HH_FREQ], STEPS, param[PARAM_HH_OFST]);

  } else {
    distribute_notes(bd_rhythm, param[PARAM_BD_NOTE], param[PARAM_BD_FREQ], STEPS, param[PARAM_BD_OFST]);
    distribute_notes(sd_rhythm, param[PARAM_SD_NOTE], param[PARAM_SD_FREQ], STEPS, param[PARAM_SD_OFST]);
    distribute_notes(hh_rhythm, param[PARAM_HH_NOTE], param[PARAM_HH_FREQ], STEPS, param[PARAM_HH_OFST]);

  }

  // depending on which button was pushed, we perform an action
  switch (lcd_key) {


    case BTN_RIGHT: {
        if ((repeat_count == 0) || (repeat_count > bpm / 2)) {
          if (param[menu_index] < param_max[menu_index]) {
            param[menu_index]++;
          }
        }
        break;

      }
    case BTN_LEFT: {
        if ((repeat_count == 0) || (repeat_count > bpm / 2)) {
          if (param[menu_index] > param_min[menu_index]) {
            param[menu_index]--;
          }
        }
        break;
      }

    case BTN_UP: {
        if (repeat_count == 0) {
          menu_index--;
          if (menu_index < 0) {
            menu_index = 0;
          }
        }
        break;
      }
    case BTN_DOWN: {
        if (repeat_count == 0) {
          menu_index++;
          if (menu_index >= PARAM_EOF) {
            menu_index = PARAM_EOF - 1;
          }
        }
        break;
      }


    //pressing SELECT once toggles play on/off
    case BTN_SELECT: {
        if (repeat_count == 0) {
          playing = !playing;
          if (playing) {
            tick = 0;
            beat = 0;
            bar = 0;
            midiSerial.write(0xFA);      //MIDI START
          } else {
            midiSerial.write(0xFC);      //MIDI STOP
          }
        }

        //longpressing select writes paramaters to EEPROM
        if (repeat_count > bpm / 2) {
          screen_wipe('#', 20);
          for (int i = 0; i < PARAM_EOF; i++) {
            EEPROM.update(PARAM_EEPROM_BASE + i, param[i]);
          }
          repeat_count = 0;
          //           delay(500);
          screen_wipe(' ', 20);
        }

        break;
      }

  }

  last_button = lcd_key;


  midiSerial.write(0xF8);      //MIDI CLOCK TICK
  tick++;
  if (tick == 24) {
    tick = 0;
  }
  if ((playing) && (tick % 6 == 0)) {
    do_sequencer_step(beat);
    beat++;
    if (beat == 16) {
      beat = 0;
      bar++;
    }
  }

  if (beat % 2 == 0) {
    delayUntil = loopStart + (600000L * 2 * param[PARAM_SWING] / (24L * bpm));
  } else {
    delayUntil = loopStart + (600000L * 2 * (100 - param[PARAM_SWING]) / (24L * bpm));
  }

  //burn cycles until we are ready for the next click
  for (; micros() < delayUntil;) {}

}
