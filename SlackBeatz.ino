
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

#define PARAM_EEPROM_BASE 0

#define PARAM_BPM      0
#define PARAM_BD_FREQ  1
#define PARAM_SD_FREQ  2
#define PARAM_HH_FREQ  3
#define PARAM_DRUM_CH  4
#define PARAM_BASS_CH  5
#define PARAM_EOF      6//must be last in list

#define BEAT_CHAR_PAUSED_1 0x9A
#define BEAT_CHAR_PAUSED_2 0xA5
#define BEAT_CHAR_PLAYING 0x2A

#define bpm param[PARAM_BPM]

#define MENU_SIZE PARAM_EOF

const char * menu_items[MENU_SIZE];

int param_min[MENU_SIZE];
int param_max[MENU_SIZE];

byte param[MENU_SIZE];

char * beat_display = "________________";

signed char menu_index = 0;
bool  playing = false;
byte tick = 0;            // 24 clock ticks per quarter note
byte beat = 0;            // where in this
int bar = 0;
int last_button;
int repeat_count;

#define ADD_MENU_ITEM(__idx,__name,__min,__max) \
  menu_items[__idx]=__name;\
  param_min[__idx]=__min;\
  param_max[__idx]=__max;


void setup() {

  ADD_MENU_ITEM(PARAM_BPM,    "BPM     : ", 30, 180);
  ADD_MENU_ITEM(PARAM_BD_FREQ, "BD FREQ : ", 0, 16);
  ADD_MENU_ITEM(PARAM_SD_FREQ, "SD FREQ : ", 0, 16);
  ADD_MENU_ITEM(PARAM_HH_FREQ, "HH FREQ : ", 0, 16);
  ADD_MENU_ITEM(PARAM_DRUM_CH, "DRUM CH : ", 1, 16);
  ADD_MENU_ITEM(PARAM_BASS_CH, "BASS CH : ", 1, 16);

   //  Set MIDI baud rate:
   midiSerial.begin(31250);

  
  //load params from EEPROM
  for (int i = 0; i < PARAM_EOF; i++) {
    param[i] = EEPROM.read(PARAM_EEPROM_BASE + i);
    if (param[i] > param_max[i]) {
      param[i] = param_max[i];
    }

    if (param[i] < param_min[i]) {
      param[i] = param_min[i];
    }
  }
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


  //debug output on UART
  Serial.begin(9600);

  show_menu();

}

int read_LCD_buttons() {              // read the buttons
  adc_key_in = analogRead(0);       // read the value from the sensor

  // my buttons when read are centered at these valies: 0, 144, 329, 504, 741
  // we add approx 50 to those values and check to see if we are close
  // We make this the 1st option for speed reasons since it will be the most likely result

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
  lcd.print(param[menu_index]);
  lcd.print("  ");    //overwrite any remaining digits
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
          screen_wipe('*', 20);
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
    beat++;
    if (beat == 16) {
      beat = 0;
      bar++;
    }
  }
  delayUntil = loopStart + (60000000L / (24L * bpm));


  //burn cycles until we are ready for the next click
  for (; micros() < delayUntil;) {}


Serial.println(millis());
}
