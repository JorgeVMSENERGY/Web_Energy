"""Optional native CPU authoring backend for the same local Marian model.

Requires onnxruntime==1.17.3, tokenizers==0.20.3, numpy==1.24.4.
Downloads open model weights; website copy never leaves the computer.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import time
import re
from urllib.request import urlretrieve

TOOLS = Path(os.environ.get('TEMP', '/tmp')) / 'vms-translation-tools'
if TOOLS.exists():
    sys.path.insert(0, str(TOOLS))
import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

ROOT = Path(__file__).resolve().parents[1]
MODEL = Path(os.environ.get('TEMP', '/tmp')) / 'vms-translation-model/native'
FILES = ['config.json', 'tokenizer.json', 'onnx/encoder_model_quantized.onnx',
         'onnx/decoder_model_merged_quantized.onnx']
PROTECTED_NAMES = ['Schneider Electric', 'Schneider', 'Sigenergy', 'SigenStack', 'Bently Nevada',
                   'Leroy Somer', 'WEG', 'TRIOL', 'SINAMICS', 'ACS880', 'ACS580',
                   'Altivar Process 980', 'Altivar 980', 'Altivar Process', 'Clean-in-Place']
NAME_PATTERN = re.compile(r'\b(?:' + '|'.join(re.escape(v) for v in sorted(PROTECTED_NAMES, key=len, reverse=True)) + r')\b', re.I)


def chunks(text, limit=400):
    sentences, start = [], 0
    for match in re.finditer(r'(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¿¡])', text):
        previous = re.search(r'(\w+)\.$', text[:match.start()])
        if previous and previous[1].lower() in {'art', 'fr', 'sr', 'sra', 'dr', 'dra', 'ing', 'lic', 'no', 'fig', 'ej', 'aprox', 'min', 'max'}:
            continue
        sentences.append(text[start:match.start()])
        start = match.end()
    if sentences:
        sentences.append(text[start:])
        return [part for sentence in sentences for part in chunks(sentence, limit)]
    if len(text) <= limit:
        return [text]
    result, current = [], ''
    for word in text.split():
        if current and len(current) + len(word) + 1 > limit:
            result.append(current)
            current = ''
        current += (' ' if current else '') + word
    if current:
        result.append(current)
    return result


def download(name):
    target = MODEL / name
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + '.tmp')
        urlretrieve('https://huggingface.co/Xenova/opus-mt-es-en/resolve/main/' + name, str(temporary))
        temporary.replace(target)
    print('Model file ready: ' + name, flush=True)


class LocalModel:
    def __init__(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(download, FILES))
        config = json.loads((MODEL / 'config.json').read_text(encoding='utf-8'))
        self.pad = config['pad_token_id']
        self.eos = config['eos_token_id']
        self.start = config['decoder_start_token_id']
        self.heads = config['decoder_attention_heads']
        self.width = config['d_model'] // self.heads
        tokenizer = json.loads((MODEL / 'tokenizer.json').read_text(encoding='utf-8'))
        # Transformers.js exports Marian's absent precompiled charmap as null.
        # NFKC matches the normalization used for this site's Latin-script text.
        tokenizer['normalizer'] = {'type': 'NFKC'}
        tokenizer['pre_tokenizer'] = {'type': 'Metaspace', 'replacement': '▁', 'prepend_scheme': 'always', 'split': True}
        tokenizer['decoder'] = {'type': 'Metaspace', 'replacement': '▁', 'prepend_scheme': 'always', 'split': True}
        self.tokenizer = Tokenizer.from_str(json.dumps(tokenizer, ensure_ascii=False))
        self.tokenizer.enable_padding(pad_id=self.pad, pad_token='<pad>')
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        opts.intra_op_num_threads = 4
        opts.inter_op_num_threads = 1
        self.encoder = ort.InferenceSession(str(MODEL / FILES[2]), opts, providers=['CPUExecutionProvider'])
        self.decoder = ort.InferenceSession(str(MODEL / FILES[3]), opts, providers=['CPUExecutionProvider'])
        self.inputs = [item.name for item in self.decoder.get_inputs()]
        self.outputs = [item.name for item in self.decoder.get_outputs()]
        print('Local CPU model ready.', flush=True)

    def translate(self, texts):
        protected, substitutions = [], []
        for text in texts:
            available = [name for name in ['Google', 'Microsoft', 'Amazon', 'Apple', 'Samsung', 'Intel', 'Oracle', 'Sony', 'Cisco', 'Nokia', 'Toyota', 'Ford', 'Dell', 'Honda', 'Canon', 'Toshiba'] if name.lower() not in text.lower()]
            mapping, assigned = {}, {}
            def protect(match):
                original = match[0]
                if original not in assigned:
                    placeholder = available[len(assigned)]
                    assigned[original] = placeholder
                    mapping[placeholder] = original
                return assigned[original]
            protected.append(NAME_PATTERN.sub(protect, text))
            substitutions.append(mapping)
        encoded = self.tokenizer.encode_batch(protected)
        ids = np.array([item.ids for item in encoded], dtype=np.int64)
        mask = np.array([item.attention_mask for item in encoded], dtype=np.int64)
        hidden = self.encoder.run(None, {'input_ids': ids, 'attention_mask': mask})[0]
        batch = len(texts)
        decoder_ids = np.full((batch, 1), self.start, dtype=np.int64)
        cache = {name: np.zeros((batch, self.heads, 0, self.width), dtype=np.float32)
                 for name in self.inputs if name.startswith('past_key_values.')}
        histories = [[] for _ in texts]
        finished = [False] * batch
        limit = min(256, max(12, (max(map(len, texts)) + 1) // 2))
        for step in range(limit):
            feed = {'input_ids': decoder_ids, 'encoder_hidden_states': hidden,
                    'encoder_attention_mask': mask, 'use_cache_branch': np.array([step > 0], dtype=np.bool_)}
            feed.update(cache)
            result = self.decoder.run(None, feed)
            scores = result[0][:, -1, :]
            for row, history in enumerate(histories):
                if len(history) >= 3:
                    tail = history[-3:]
                    for previous in range(len(history)-3):
                        if history[previous:previous+3] == tail:
                            scores[row, history[previous+3]] = -np.inf
            following = scores.argmax(axis=-1)
            for row, token in enumerate(following):
                if finished[row]:
                    continue
                if token == self.eos:
                    finished[row] = True
                else:
                    histories[row].append(int(token))
            if all(finished):
                break
            decoder_ids = following.reshape(batch, 1).astype(np.int64)
            for name, value in zip(self.outputs[1:], result[1:]):
                key = name.replace('present.', 'past_key_values.', 1)
                if key in cache and value.size:
                    cache[key] = value
        outputs = self.tokenizer.decode_batch(histories, skip_special_tokens=True)
        for row, mapping in enumerate(substitutions):
            for placeholder, original in mapping.items():
                outputs[row] = re.sub(r'\b' + re.escape(placeholder) + r'\b', lambda _: original, outputs[row], flags=re.I)
        return outputs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--repair', action='store_true', help='Review anomalously short or numerically incomplete machine translations')
    parser.add_argument('--repair-brands', action='store_true', help='Preserve brand and platform names in machine translations')
    args = parser.parse_args()
    model = LocalModel()
    start = time.time()
    print('Sample: ' + str(model.translate(['Soluciones eléctricas industriales', 'Sistema de puesta a tierra'])), flush=True)
    print('Sample time: ' + str(round(time.time()-start, 2)), flush=True)
    if not args.run and not args.repair and not args.repair_brands:
        return
    path = ROOT / 'scripts/english-translations.json'
    cache = json.loads(path.read_text(encoding='utf-8'))
    if args.repair_brands:
        editorial = json.loads((ROOT / 'scripts/english-overrides.json').read_text(encoding='utf-8'))
        pending = [key for key, value in cache.items() if key not in editorial and any(
            not re.search(r'\b' + re.escape(match[0]) + r'\b', value, re.I) for match in NAME_PATTERN.finditer(key))]
    elif args.repair:
        from build_english import NUMBER, human
        editorial = json.loads((ROOT / 'scripts/english-overrides.json').read_text(encoding='utf-8'))
        pending = [key for key, value in cache.items() if key not in editorial and human(key) and len(key) > 40 and
                   ((len(key) > 80 and len(value) < len(key)*.72) or len(NUMBER.findall(key)) != len(NUMBER.findall(value)))]
    else:
        pending = json.loads((ROOT / 'scripts/english-pending.json').read_text(encoding='utf-8'))
        pending = [key for key in pending if key not in cache]
    pending = sorted(pending, key=len)
    started = time.time()
    for index in range(0, len(pending), 16):
        keys = pending[index:index+16]
        groups = [chunks(key) for key in keys]
        flat = [part for group in groups for part in group]
        translated = []
        for offset in range(0, len(flat), 16):
            translated.extend(model.translate(flat[offset:offset+16]))
        offset = 0
        for key, group in zip(keys, groups):
            cache[key] = ' '.join(translated[offset:offset+len(group)])
            offset += len(group)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        temporary.replace(path)
        if index % 64 == 0 or index+16 >= len(pending):
            print(f'Native local translations: {min(index+16,len(pending))}/{len(pending)}; {time.time()-started:.1f}s', flush=True)


if __name__ == '__main__':
    main()
