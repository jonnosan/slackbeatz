
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


#define BEATS_PER_BAR  16

enum PRM_TYPES {
  PRM_TYPE_NUM,
  PRM_TYPE_TOGGLE,
  PRM_TYPE_SLIDER,
  PRM_TYPE_SCALE,
  PRM_TYPE_CHORD,
  PRM_TYPE_CHORD_STYLE,
  PRM_TYPE_RIFF_STYLE,
};

#define PRM_TOGGLE_ON 1
#define PRM_TOGGLE_OFF 0

#define PARAM_EEPROM_BASE 0

enum PRM_SLIDER {
  PRM_SLIDER_OFF,
  PRM_SLIDER_LOW,
  PRM_SLIDER_MED,
  PRM_SLIDER_HI
};

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
  PARAM_CHORD_CH,
  PARAM_PROG_ROOT,
  PARAM_SCALE_TYPE,
  PARAM_CHORD_1,
  PARAM_CHORD_2,
  PARAM_CHORD_3,
  PARAM_CHORD_4,
  PARAM_CHORD_STYLE,
  PARAM_RIFF_STYLE,
  PARAM_RIFF_CH,
  PARAM_EOF  //must be last item
};



#define MENU_PLAY 0
#define  MENU_DRUMS 1
#define  MENU_CHORDS 2
#define NUM_MENUS 3

const char * MENU_NAMES[NUM_MENUS] = {
  "PLAY ",
  "Drum Settings",
  "Accompaniment",
};




#define MENU_MAX_SIZE 16

#define BEAT_CHAR_PAUSED_1 0x9A
#define BEAT_CHAR_PAUSED_2 0xA5


#define STEPS BEATS_PER_BAR
#define MAX_NOTE 127
#define DRUM_VELOCITY 100
#define CHORD_VELOCITY 127
#define RIFF_VELOCITY 100

#define FILL_BARS 4
#define bpm param[PARAM_BPM]


byte bd_rhythm[STEPS];
byte sd_rhythm[STEPS];
byte hh_rhythm[STEPS];


signed char  menu_item_index[NUM_MENUS][MENU_MAX_SIZE];
signed char  menu_size[NUM_MENUS];

const char * param_name[PARAM_EOF];
byte param_type[PARAM_EOF];
byte param_min[PARAM_EOF];
byte param_max[PARAM_EOF];
byte param_default[PARAM_EOF];

byte param[PARAM_EOF];


const char * SLIDER_VAL_NAMES[] = {
  "OFF",
  "LOW",
  "MED",
  "HI"
};

const char * SCALE_NAMES[] = {
  "Minor",
  "Major",
};

#define SCALE_LENGTH 14
const byte  SCALES[2][SCALE_LENGTH] = {
  {0, 2, 3, 5, 7, 8, 10, 12 + 0, 12 + 2, 12 + 3, 12 + 5, 12 + 7, 12 + 8, 12 + 10, }, //Minor
  {0, 2, 4, 5, 7, 9, 11, 12 + 0, 12 + 2, 12 + 4, 12 + 5, 12 + 7, 12 + 9, 12 + 11}, //Major
};


#define MAX_CHORD_STYLE 3
#define CHORD_STYLE_ROOT  0
#define CHORD_STYLE_TRIAD 2
#define CHORD_STYLE_FIFTH 3
#define CHORD_STYLE_OCTAVE 3


const char * CHORD_STYLE_NAMES[MAX_CHORD_STYLE + 1] = {
  "Root",
  "Triad",
  "Fifth",
  "Octave",
};

#define MAX_RIFF_STYLE 1
#define RIFF_STYLE_CHUG  0
#define RIFF_STYLE_ROLLUP  1


const char * RIFF_STYLE_NAMES[MAX_RIFF_STYLE + 1] = {
  "Chug",
  "RollUp",
};


#define NO_NOTE 0xFF
const byte CHORD_NOTES[MAX_CHORD_STYLE + 1][3] = {
  {0, NO_NOTE, NO_NOTE},	      //root only
  {0, 2, 4},		//root+third+fifth
  {0, 4, 7},	      //root+fifth+octave
  {0, 7, NO_NOTE},	      //root+octave

};


const char * CHORD_NAMES[] = {
  "I",
  "II",
  "III",
  "IV",
  "V",
  "VI",
  "VII",
  "VIII",
};


char * beat_display = "________________";

signed char menu_index = 0;
signed char param_index = 0; //in current menu, what paramater number is being looked at?
byte param_number = 0; //absolute paramater number (i.e. not relative to any particular menu)
byte menu_level = 0;
bool  playing = false;
byte tick = 0;            // 24 clock ticks per quarter note
byte beat = 0;            // where in this
int bar = 0;
int last_button;
int repeat_count;
#define MAX_CHORD_NOTES 3

byte playing_chord_notes[MAX_CHORD_NOTES];
byte last_playing_chord_notes[MAX_CHORD_NOTES];


#define INIT_MENUS \
  for(int __i=0;__i<NUM_MENUS;__i++) {menu_size[__i]=0;};

#define ADD_MENU_ITEM(__menu_idx,__param_idx,__prm_type,__name,__min,__max,__default) \
  menu_item_index[__menu_idx][menu_size[__menu_idx]]=__param_idx; \
  menu_size[__menu_idx]++; \
  param_name[__param_idx] = __name; \
  param_type[__param_idx] = __prm_type; \
  param_min[__param_idx] = __min; \
  param_max[__param_idx] = __max; \
  param_default[__param_idx] = __default;


void setup() {

  INIT_MENUS

  //menu items are displayed in the order they are added - you can rearrange order here without messing up presets
  ADD_MENU_ITEM(MENU_PLAY, PARAM_BPM,    PRM_TYPE_NUM,  "BPM     : ", 30, 180, 120);     //beats per minute
  ADD_MENU_ITEM(MENU_DRUMS, PARAM_DRUM_CH, PRM_TYPE_NUM, "Drum Ch : ", 0, 16, 10);
  ADD_MENU_ITEM(MENU_DRUMS, PARAM_BD_FREQ, PRM_TYPE_NUM, "BD Freq : ", 0, STEPS, 4);   //how frequently this drum hit occurs
  ADD_MENU_ITEM(MENU_DRUMS, PARAM_SD_FREQ, PRM_TYPE_NUM, "SD Freq : ", 0, STEPS, 7);
  ADD_MENU_ITEM(MENU_DRUMS, PARAM_HH_FREQ, PRM_TYPE_NUM, "HH Freq : ", 0, STEPS, 11);
  ADD_MENU_ITEM(MENU_DRUMS, PARAM_BD_NOTE, PRM_TYPE_NUM, "BD Note : ", 0, MAX_NOTE, 36); //MIDI note sent for 'bass drum' hits
  ADD_MENU_ITEM(MENU_DRUMS, PARAM_SD_NOTE, PRM_TYPE_NUM, "SD Note : ", 0, MAX_NOTE, 38); //MIDI note sent for 'snare drum' hits
  ADD_MENU_ITEM(MENU_DRUMS, PARAM_HH_NOTE, PRM_TYPE_NUM, "HH Note : ", 0, MAX_NOTE, 42); //MIDI note sent for 'high hat' hits
  ADD_MENU_ITEM(MENU_DRUMS, PARAM_BD_OFST, PRM_TYPE_NUM, "BD Ofst : ", 0, STEPS - 1, 0); //offset from first step of sequence when distributing drum hits
  ADD_MENU_ITEM(MENU_DRUMS, PARAM_SD_OFST, PRM_TYPE_NUM, "SD Ofst : ", 0, STEPS - 1, 0);
  ADD_MENU_ITEM(MENU_DRUMS, PARAM_HH_OFST, PRM_TYPE_NUM, "HH Ofst : ", 0, STEPS - 1, 0);
  ADD_MENU_ITEM(MENU_DRUMS, PARAM_DRUM_FILL, PRM_TYPE_TOGGLE, "Fill    : ", PRM_TOGGLE_OFF, PRM_TOGGLE_ON, PRM_TOGGLE_ON);
  ADD_MENU_ITEM(MENU_DRUMS, PARAM_SWING, PRM_TYPE_SLIDER,   "Swing   : ", PRM_SLIDER_OFF, PRM_SLIDER_HI, PRM_SLIDER_OFF);
  ADD_MENU_ITEM(MENU_CHORDS, PARAM_CHORD_CH, PRM_TYPE_NUM,    "Cord Ch : ", 0, 16, 1);
  ADD_MENU_ITEM(MENU_CHORDS, PARAM_RIFF_CH, PRM_TYPE_NUM,    "Riff Ch : ", 0, 16, 8);
  ADD_MENU_ITEM(MENU_CHORDS, PARAM_PROG_ROOT, PRM_TYPE_NUM,   "Root Note: ", 0, MAX_NOTE, 48); //c1
  ADD_MENU_ITEM(MENU_CHORDS, PARAM_SCALE_TYPE, PRM_TYPE_SCALE, "Scale    : ", 0, 1, 0); //minor
  ADD_MENU_ITEM(MENU_CHORDS, PARAM_CHORD_1, PRM_TYPE_CHORD,   "Chord 1  : ", 0, 7, 0); //I
  ADD_MENU_ITEM(MENU_CHORDS, PARAM_CHORD_2, PRM_TYPE_CHORD,   "Chord 2  : ", 0, 7, 5); //VI
  ADD_MENU_ITEM(MENU_CHORDS, PARAM_CHORD_3, PRM_TYPE_CHORD,   "Chord 3  : ", 0, 7, 1); //II
  ADD_MENU_ITEM(MENU_CHORDS, PARAM_CHORD_4, PRM_TYPE_CHORD,             "Chord 4  : ", 0, 7, 3); //IV
  ADD_MENU_ITEM(MENU_CHORDS, PARAM_CHORD_STYLE, PRM_TYPE_CHORD_STYLE,   "Style : ", 0, MAX_CHORD_STYLE, CHORD_STYLE_TRIAD);
  ADD_MENU_ITEM(MENU_CHORDS, PARAM_RIFF_STYLE, PRM_TYPE_RIFF_STYLE,   "Riff : ", 0, MAX_RIFF_STYLE, RIFF_STYLE_ROLLUP);


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


  stop_song();    //send a 'stop' in case we got reset while we were playing

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

  if (menu_level == 0) {
    if (menu_index == 0) {
      lcd.print(playing ? "Stop" : "Start");
      lcd.print(" - BPM: ");
      lcd.print(param[PARAM_BPM]);

    } else {
      lcd.print(MENU_NAMES[menu_index]);
    }
  }  else {
    lcd.print(param_name[param_number]);
    switch (param_type[param_number]) {
      case PRM_TYPE_NUM:
        lcd.print(param[param_number]);
        break;
      case PRM_TYPE_TOGGLE:
        if (param[param_number] == PRM_TOGGLE_ON) {
          lcd.print("ON");
        } else {
          lcd.print("OFF");
        };
        break;
      case PRM_TYPE_SLIDER:
        lcd.print(SLIDER_VAL_NAMES[param[param_number]]);
        break;
      case PRM_TYPE_SCALE:
        lcd.print(SCALE_NAMES[param[param_number]]);
        break;
      case PRM_TYPE_CHORD:
        lcd.print(CHORD_NAMES[param[param_number]]);
        break;
      case PRM_TYPE_CHORD_STYLE:
        lcd.print(CHORD_STYLE_NAMES[param[param_number]]);
        break;
      case PRM_TYPE_RIFF_STYLE:
        lcd.print(RIFF_STYLE_NAMES[param[param_number]]);
        break;

    }

  }
  lcd.print("            ");    //overwrite any remaining chars on this line
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


void start_song() {
  tick = 0;
  beat = 0;
  bar = 0;
  midiSerial.write(0xFA);      //MIDI START
  for (byte i = 0; i < MAX_CHORD_NOTES; i++) {
    playing_chord_notes[i] = NO_NOTE;
    last_playing_chord_notes[i] = NO_NOTE;
  }

}


void stop_song() {
  midiSerial.write(0xFC);      //MIDI STOP


  all_notes_off(param[PARAM_DRUM_CH]);
  all_notes_off(param[PARAM_CHORD_CH]);
  all_notes_off(param[PARAM_RIFF_CH]);

}

void all_notes_off(byte channel) {
  midiSerial.write(0xAF + channel);    //CONTROL
  midiSerial.write(123);      //all notes off
  midiSerial.write((byte)0);

}

//stop all other notes this channel, play specified note
void play_solo_note(byte channel, byte note, byte velocity) {
  all_notes_off( channel);
  play_note( channel,  note,  velocity) ;
}

void play_note(byte channel, byte note, byte velocity) {
  midiSerial.write(0x8F + channel);    //note on
  midiSerial.write(note);
  midiSerial.write(velocity);

}

void do_sequencer_step(int step_number) {

  //change chords at start of each step, even if not playing on any chord channel
  if (step_number == 0) {

    //stop all current notes
    for (byte i = 0; i < MAX_CHORD_NOTES; i++) {
      if ((param[PARAM_CHORD_CH] > 0) && (playing_chord_notes[i] != NO_NOTE)) {
        midiSerial.write(0x8F + param[PARAM_CHORD_CH]);    //note on
        midiSerial.write(playing_chord_notes[i]);
        midiSerial.write((byte)0);
      }


      byte base_note_number = param[PARAM_CHORD_1 + (bar % 4)];

      for (byte i = 0; i < MAX_CHORD_NOTES; i++) {
        byte chord_note = CHORD_NOTES[param[PARAM_CHORD_STYLE]][i];
        if (chord_note == NO_NOTE) {
          playing_chord_notes[i] = NO_NOTE;
        } else {
          byte note_number = (base_note_number + chord_note) % SCALE_LENGTH;

          playing_chord_notes[i] = SCALES[param[PARAM_SCALE_TYPE]][note_number] + param[PARAM_PROG_ROOT];
          if (param[PARAM_CHORD_CH] > 0) {
            midiSerial.write(0x8F + param[PARAM_CHORD_CH]);    //note on
            midiSerial.write(playing_chord_notes[i]);
            midiSerial.write(CHORD_VELOCITY);
          }
        }
      }
    }
  }

  if (param[PARAM_DRUM_CH] > 0) {
    if (bd_rhythm[step_number] > 0) {
      play_note(param[PARAM_DRUM_CH], bd_rhythm[step_number], DRUM_VELOCITY + 1);
    }

    if (hh_rhythm[step_number] > 0) {
      play_note(param[PARAM_DRUM_CH], hh_rhythm[step_number], DRUM_VELOCITY + 2);
    }

    if (sd_rhythm[step_number] > 0) {
      play_note(param[PARAM_DRUM_CH], sd_rhythm[step_number], DRUM_VELOCITY + 3);
    }
  }


  if (param[PARAM_RIFF_CH] > 0)  {
    switch (param[PARAM_RIFF_STYLE]) {
      case RIFF_STYLE_CHUG:
        //base note every 2nd beat
        if (step_number % 2 == 0) {
          play_solo_note(param[PARAM_RIFF_CH], playing_chord_notes[0], RIFF_VELOCITY);
        }
        break;

      case RIFF_STYLE_ROLLUP:
        Serial.print("ROLLUP");
        Serial.println(step_number);

        byte this_note = NO_NOTE;
        switch (step_number) {
          case 0:
          case 4:
          case 7:
            this_note = playing_chord_notes[0];
            break;
          case 8:
            this_note = playing_chord_notes[1];
            if (this_note == NO_NOTE) {
              this_note = playing_chord_notes[0];
              Serial.println("reset 1");

            }
            break;
          case 12:
          case 15:
            this_note = playing_chord_notes[2];
            if (this_note == NO_NOTE) {
              this_note = playing_chord_notes[0];
              Serial.println("reset 2");
            }
            break;
        }
        if (this_note != NO_NOTE) {
          play_solo_note(param[PARAM_RIFF_CH], this_note, RIFF_VELOCITY);
        }
        break;


    }

  }




}

//does the current 'tick' represent a move to the next 'beat' for current 'swing' setting?
bool tick_is_full_beat() {
  return ((tick == 0) || (tick == 6 + param[PARAM_SWING]) || (tick == 12) || (tick == 18 + param[PARAM_SWING]));
}

void loop() {


  unsigned long loopStart;
  unsigned long delayUntil;
  loopStart = micros();

  beat_display[beat] = playing ? ('1' + (bar % 4)) : ((tick > 11) ? BEAT_CHAR_PAUSED_1 : BEAT_CHAR_PAUSED_2);
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

    distribute_notes(bd_rhythm, param[PARAM_BD_NOTE], STEPS - param[PARAM_BD_FREQ], STEPS, param[PARAM_BD_OFST]);
    distribute_notes(sd_rhythm, param[PARAM_HH_NOTE], 2 + param[PARAM_SD_FREQ] % STEPS, STEPS, param[PARAM_SD_OFST]);
    distribute_notes(hh_rhythm, param[PARAM_SD_NOTE], 2 + param[PARAM_HH_FREQ] % STEPS, STEPS, param[PARAM_HH_OFST]);

  } else {
    distribute_notes(bd_rhythm, param[PARAM_BD_NOTE], param[PARAM_BD_FREQ], STEPS, param[PARAM_BD_OFST]);
    distribute_notes(sd_rhythm, param[PARAM_SD_NOTE], param[PARAM_SD_FREQ], STEPS, param[PARAM_SD_OFST]);
    distribute_notes(hh_rhythm, param[PARAM_HH_NOTE], param[PARAM_HH_FREQ], STEPS, param[PARAM_HH_OFST]);
  }

  //longpressing select writes paramaters to EEPROM
  if ((lcd_key == BTN_SELECT) && (repeat_count > bpm / 2)) {
    screen_wipe('#', 20);
    for (int i = 0; i < PARAM_EOF; i++) {
      EEPROM.update(PARAM_EEPROM_BASE + i, param[i]);
    }
    repeat_count = 0;
    //           delay(500);
    screen_wipe(' ', 20);
  }


  // depending on which screen we are on and which button was pushed, we perform an action
  if (menu_level == 0) {// we are at the 'top' level

    switch (lcd_key) {

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
            if (menu_index >= NUM_MENUS) {
              menu_index = NUM_MENUS - 1;
            }

          }
          break;
        }
      case BTN_SELECT: {
          if (repeat_count == 0) {

            //on 'PLAY' menu, select means start/stop. otherwise it means go to a submenu
            if (menu_index == 0) {

              playing = !playing;
              if (playing) {
                start_song();
              } else {
                stop_song();
              }
            } else { //not 'PLAY' menu, so go to submenu
              menu_level = 1;
              param_index = 0; //in current menu, what paramater number is being looked at?
              param_number = menu_item_index[menu_index][param_index];

            }

          }

        }
        break;

      //in top level mode, left and right always alter BPM
      case BTN_RIGHT: {
          if ((repeat_count == 0) || (repeat_count > bpm / 2)) {
            if (param[PARAM_BPM] < param_max[PARAM_BPM]) {
              param[PARAM_BPM]++;
            }
          }

          break;

        }
      case BTN_LEFT: {
          if ((repeat_count == 0) || (repeat_count > bpm / 2)) {
            if (param[PARAM_BPM] > param_min[PARAM_BPM]) {
              param[PARAM_BPM]--;
            }
          }
          break;
        }

    }

  } else {   //we are in an individual menu

    switch (lcd_key) {


      case BTN_RIGHT: {
          if ((repeat_count == 0) || (repeat_count > bpm / 2)) {
            if (param[param_number] < param_max[param_number]) {
              param[param_number]++;
            }
          }

          break;

        }
      case BTN_LEFT: {
          if ((repeat_count == 0) || (repeat_count > bpm / 2)) {
            if (param[param_number] > param_min[param_number]) {
              param[param_number]--;
            }
          }
          break;
        }

      case BTN_UP: {
          if (repeat_count == 0) {
            param_index--;
            if (param_index < 0) {
              param_index = 0;
            }
          }
          param_number = menu_item_index[menu_index][param_index];
          break;
        }
      case BTN_DOWN: {
          if (repeat_count == 0) {
            param_index++;
            if (param_index >= menu_size[menu_index]) {
              param_index = menu_size[menu_index] - 1;
            }
          }
          param_number = menu_item_index[menu_index][param_index];
          break;
        }


      //pressing SELECT inside a param menu goes back to top level menu
      case BTN_SELECT: {
          if (repeat_count == 0) {
            menu_index = 0;
            menu_level = 0;
          }
          break;
        }

    }


  }
  last_button = lcd_key;
  midiSerial.write(0xF8);      //MIDI CLOCK TICK
  tick++;
  if (tick == 24) {
    tick = 0;
  }
  if ((playing) && (tick_is_full_beat())) {
    do_sequencer_step(beat);
    beat++;
    if (beat == BEATS_PER_BAR) {
      beat = 0;
      bar++;
    }
  }

  delayUntil = loopStart + (60000000L / (24L * bpm));

  //burn cycles until we are ready for the next click
  for (; micros() < delayUntil;) {}

}
