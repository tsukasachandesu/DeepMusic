import numpy as np
from copy import deepcopy
from chorder import Dechorder
from midi2audio import FluidSynth

from miditoolkit.midi import parser as mid_parser
from miditoolkit.midi.containers import Marker

from .modules import *
from .const import Constants

def midi_to_audio(midi_path, audio_path, sf2_path='assets/soundfonts/general.sf2'):
    FluidSynth(sf2_path).midi_to_audio(midi_path, audio_path)

def sort_bar_beats(bar):
    assert isinstance(bar[0], Metric) and bar[0].position == 0, "First event in a bar should be a Bar()"
    poses = {}
    prev_pos = None
    for e in bar:
        if isinstance(e, Metric):
            prev_pos = e
            poses[prev_pos] = []
        elif isinstance(e, Note):
            poses[prev_pos] += [e]

    res = []
    for pos in sorted(poses, key=lambda x: x.position):
        res += [pos] + poses[pos]
    return res

def remove_excess_pos(seq):
    is_empty_beat = lambda e: e.tempo is None and e.chord is None
    is_duplicate = lambda e1, e2 : e1.position == e2.position
    condition = lambda e1, e2 : isinstance(e1, Metric) and isinstance(e2, Metric) and e1.position > 0
    res = []
    for i,e in enumerate(seq[:-1]):
        if condition and (is_empty_beat(e) or is_duplicate(e, seq[i+1])):
            continue
        else:
            res += [e]
    if not (isinstance(seq[-1], Metric) and seq[-1].position > 0 and is_empty_beat(seq[-1])):
        res += [seq[-1]]
    return res

def flatten(ls):
    res = []
    for l in ls:
        res += l
    return res

def clean_cp(cp):
    for c in cp:
        if c[0] == 0:
            c[4:] = 0
        if c[0] == 1:
            c[1:4] = 0
    return cp

def compare_bars(bar1, bar2):
    last_pos1 = set()
    last_pos2 = set()
    for e1, e2 in zip(bar1.events, bar2.events):
        if isinstance(e1, Metric) and isinstance(e2, Metric):
            if e1 != e2 or last_pos1 != last_pos2:
                return False
            last_pos1 = set()
            last_pos2 = set()
        elif isinstance(e1, Note) and isinstance(e2, Note):
            last_pos1.update([e1])
            last_pos2.update([e2])
        else:
            return False
    return True


def merge_bars(bars : dict, key_inst : str):
    poses = dict(
        [(i, 
          {
              'empty':True, 
              'chord' : None,
              'tempo' : None,
              'notes':dict([(inst, []) for inst in bars])
          }
         )
         for i in range(bars[key_inst].const.unit * 4)]
    )
    for inst in bars:
        prev_pos = 0
        for e in bars[inst].events:
            if isinstance(e, Metric):
                prev_pos = e.position
                poses[prev_pos]['empty'] = False
                if inst == key_inst:
                    poses[prev_pos]['chord'] = e.chord
                    poses[prev_pos]['tempo'] = e.tempo
            elif isinstance(e, Note):
                poses[prev_pos]['notes'][inst] += [e]

    res = []
    for pos in poses:
        if not poses[pos]['empty']:
            res += [Metric(position=pos, tempo=poses[pos]['tempo'], chord=poses[pos]['chord'])]
            for inst in poses[pos]['notes']:
                res += poses[pos]['notes'][inst]
    return res


def analyze_midi(midi):
    const = Constants()
    midi = deepcopy(midi)
    chords = Dechorder.dechord(midi)
    markers = []
    prev = None
    for cidx, chord in enumerate(chords):
        if chord.is_complete():
            chord_text = 'Chord_' + const.pitch_classes[chord.root_pc] + '_' + chord.quality
            if chord_text != prev:
                markers.append(Marker(time=int(cidx*midi.ticks_per_beat), text=chord_text))
                prev = chord_text
    midi.markers = markers
    return midi

def edit_time(time, offset, step):
    time = max(0, time - offset)
    return int(np.round(time/step)*step)

def quantize_midi(
    midi, 
    unit=12,
    min_tempo=30,
    max_tempo=300,
    num_tempo_bins=30, 
    num_velocity_bins=30):

    midi = deepcopy(midi)
    ## load notes
    instr_notes = []
    for instr in midi.instruments:
        notes = []
        for note in instr.notes:
            notes += [note]
        instr_notes += [sorted(notes, key=lambda x: (x.start, x.pitch))]
    
    offset = min([notes[0].start for notes in instr_notes])
    tick_resol = midi.ticks_per_beat
    const = Constants(unit, tick_resol, min_tempo, max_tempo, num_tempo_bins, num_velocity_bins)

    for i,notes in enumerate(instr_notes):
        for note in notes:
            note.start = edit_time(note.start, offset, const.step)
            note.end = edit_time(note.end, offset, const.step)
            note.end = min(note.end, edit_time(note.start + const.bar_resol, offset, const.step))
            note.velocity = const.velocity_bins[
                np.argmin(abs(const.velocity_bins - note.velocity))
            ]
        midi.instruments[i].notes = sorted(notes, key=lambda x: (x.start, x.pitch))

    # load chords
    chords = []
    for marker in midi.markers:
        if marker.text.startswith('Chord'):
            marker.time = edit_time(marker.time, offset, const.step)
            chords.append(marker)
    chords.sort(key=lambda x: x.time)
    midi.markers = chords
    

    # load tempos
    tempos = midi.tempo_changes
    for tempo in tempos:
        tempo.time = edit_time(tempo.time, offset, const.step)
        tempo.tempo = const.tempo_bins[
            np.argmin(abs(const.tempo_bins - tempo.tempo))
        ]
    tempos.sort(key=lambda x: x.time)
    midi.tempo_changes = tempos

    midi.max_tick = edit_time(midi.max_tick, offset, const.step)
    return midi

def process_midi(
    file_path, 
    save_path, 
    unit=12,
    min_tempo=30,
    max_tempo=300,
    num_tempo_bins=30, 
    num_velocity_bins=30):

    try:
        midi = mid_parser.MidiFile(file_path)
        times = midi.time_signature_changes
        if len(times):
            t = times[0]
            if t.numerator != 4 or t.denominator != 4:
                return
        midi = analyze_midi(midi)
        midi = quantize_midi(midi, unit, min_tempo, max_tempo, num_tempo_bins, num_velocity_bins)
        midi.dump(save_path + file_path.split('/')[-1])
    except Exception as e:
        print(file_path, 'caused error', e)