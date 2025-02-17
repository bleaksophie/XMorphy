from tensorflow.keras.models import Model
from tensorflow.keras.layers import Conv1D
from tensorflow.keras.layers import Dense, Input, Concatenate, BatchNormalization
from tensorflow.keras.layers import TimeDistributed, Dropout, MaxPooling1D, Activation
from tensorflow.keras.utils import to_categorical
from tensorflow.keras.callbacks import EarlyStopping
from tensorflow.keras.preprocessing.sequence import pad_sequences
import tensorflow.keras as keras
import numpy as np
import time
from enum import Enum

from argparse import ArgumentParser

SPEECH_PARTS = [
    'X',
    'ADJ',
    'ADV',
    'INTJ',
    'NOUN',
    'PROPN',
    'VERB',
    'ADP',
    'AUX',
    'CONJ',
    'SCONJ',
    'DET',
    'NUM',
    'PART',
    'PRON',
    'PUNCT',
    'GRND',
    'H',
    'R',
    'Q',
    'SYM',
]

SPEECH_PART_MAPPING = {str(s): num for num, s in enumerate(SPEECH_PARTS)}

MASK_VALUE = 0.0


def build_speech_part_array(sp):
    output = [0. for _ in range(len(SPEECH_PARTS))]
    output[SPEECH_PART_MAPPING[str(sp)]] = 1.
    return output


PARTS_MAPPING = {
    'UNKN': 0,
    'PREF': 1,
    'ROOT': 2,
    'SUFF': 3,
    'END': 4,
    'LINK': 5,
    'HYPH': 6,
    'POSTFIX': 7,
    'B-SUFF': 8,
    'B-PREF': 9,
    'B-ROOT': 10,
}

LETTERS = {
    'о': 1,
    'е': 2,
    'а': 3,
    'и': 4,
    'н': 5,
    'т': 6,
    'с': 7,
    'р': 8,
    'в': 9,
    'л': 10,
    'к': 11,
    'м': 12,
    'д': 13,
    'п': 14,
    'у': 15,
    'я': 16,
    'ы': 17,
    'ь': 18,
    'г': 19,
    'з': 20,
    'б': 21,
    'ч': 22,
    'й': 23,
    'х': 24,
    'ж': 25,
    'ш': 26,
    'ю': 27,
    'ц': 28,
    'щ': 29,
    'э': 30,
    'ф': 31,
    'ъ': 32,
    'ё': 33,
    '-': 34,
}


VOWELS = {
    'а', 'и', 'е', 'ё', 'о', 'у', 'ы', 'э', 'ю', 'я'
}


class MorphemeLabel(Enum):
    UNKN = 'UNKN'
    PREF = 'PREF'
    ROOT = 'ROOT'
    SUFF = 'SUFF'
    END = 'END'
    LINK = 'LINK'
    HYPH = 'HYPH'
    POSTFIX = 'POSTFIX'
    NONE = None


class Morpheme(object):
    def __init__(self, part_text, label, begin_pos):
        self.part_text = part_text
        self.length = len(part_text)
        self.begin_pos = begin_pos
        self.label = label
        self.end_pos = self.begin_pos + self.length

    def __len__(self):
        return self.length

    def get_labels(self):
        if self.length == 1:
            return ['S-' + self.label.value]
        result = ['B-' + self.label.value]
        result += ['M-' + self.label.value for _ in self.part_text[1:-1]]
        result += ['E-' + self.label.value]
        return result

    def get_simple_labels(self):
        if (self.label == MorphemeLabel.SUFF or self.label == MorphemeLabel.PREF or self.label == MorphemeLabel.ROOT):

            result = ['B-' + self.label.value]
            if self.length > 1:
                result += [self.label.value for _ in self.part_text[1:]]
            return result
        else:
            return [self.label.value] * self.length

    def __str__(self):
        return self.part_text + ':' + self.label.value

    @property
    def unlabeled(self):
        return not self.label.value


class Word(object):
    def __init__(self, morphemes=[], speech_part='X'):
        self.morphemes = morphemes
        self.sp = speech_part

    def append_morpheme(self, morpheme):
        self.morphemes.append(morpheme)

    def get_word(self):
        return ''.join([morpheme.part_text for morpheme in self.morphemes])

    def parts_count(self):
        return len(self.morphemes)

    def suffix_count(self):
        return len([morpheme for morpheme in self.morphemes
                    if morpheme.label == MorphemeLabel.SUFFIX])

    def get_labels(self):
        result = []
        for morpheme in self.morphemes:
            result += morpheme.get_labels()
        return result

    def get_simple_labels(self):
        result = []
        for morpheme in self.morphemes:
            result += morpheme.get_simple_labels()
        return result

    def __str__(self):
        return '/'.join([str(morpheme) for morpheme in self.morphemes])

    def __len__(self):
        return sum(len(m) for m in self.morphemes)

    @property
    def unlabeled(self):
        return all(p.unlabeled for p in self.morphemes)


def parse_morpheme(str_repr, position):
    #print(str_repr)
    text, label = str_repr.split(':')
    return Morpheme(text, MorphemeLabel[label], position)


def parse_word(str_repr):
    if str_repr.count('\t') == 3:
        wordform, word_parts, _, class_info = str_repr.split('\t')
        if 'ADJ' in class_info:
            sp = 'ADJ'
        elif 'VERB' in class_info:
            sp = 'VERB'
        elif 'NOUN' in class_info:
            sp = 'NOUN'
        elif 'GRND' in class_info:
            sp = 'GRND'
        elif 'ADV' in class_info:
            sp = 'ADV'
        elif 'PART' in class_info:
            sp = 'PART'
        else:
            raise Exception("Unknown class", class_info)
    elif str_repr.count('\t') == 2:
        wordform, word_parts, sp = str_repr.split('\t')
    else:
        wordform, word_parts = str_repr.split('\t')
        sp = 'X'

    if ':' in wordform or '/' in wordform:
        return None

    parts = word_parts.split('/')
    morphemes = []
    global_index = 0
    for part in parts:
        morphemes.append(parse_morpheme(part, global_index))
        global_index += len(part)
    return Word(morphemes, sp)


def measure_quality(predicted_targets, targets, words, verbose=False):
    TP, FP, FN, equal, total = 0, 0, 0, 0, 0
    SE = ['{}-{}'.format(x, y) for x in "SE" for y in ["ROOT", "PREF", "SUFF", "END", "LINK", "None"]]
    corr_words = 0
    for corr, pred, word in zip(targets, predicted_targets, words):
        corr_len = len(corr)
        pred_len = len(pred)
        boundaries = [i for i in range(corr_len) if corr[i] in SE]
        pred_boundaries = [i for i in range(pred_len) if pred[i] in SE]
        common = [x for x in boundaries if x in pred_boundaries]
        TP += len(common)
        FN += len(boundaries) - len(common)
        FP += len(pred_boundaries) - len(common)
        equal += sum(int(x == y) for x, y in zip(corr, pred))
        total += len(corr)
        corr_words += (corr == pred)
        if corr != pred and verbose:
            print("Error in word '{}':\n correct:".format(word.get_word()), corr, '\n!=\n wrong:', pred)

    metrics = ["Precision", "Recall", "F1", "Accuracy", "Word accuracy"]
    results = [TP / (TP+FP), TP / (TP+FN), TP / (TP + 0.5*(FP+FN)),
               equal / total, corr_words / len(targets)]
    return list(zip(metrics, results))


def _get_parse_repr(word):
    features = []
    word_text = word.get_word()
    for index, letter in enumerate(word_text):
        letter_features = []
        vovelty = 0
        if letter in VOWELS:
            vovelty = 1
        letter_features.append(vovelty)
        if letter in LETTERS:
            letter_code = LETTERS[letter]
        else:
            letter_code = 0
        letter_features += to_categorical(letter_code, num_classes=len(LETTERS) + 1).tolist()
        letter_features += build_speech_part_array(word.sp)
        features.append(letter_features)

    X = np.array(features, dtype=np.int8)
    Y = np.array([to_categorical(PARTS_MAPPING[label], num_classes=len(PARTS_MAPPING)) for label in word.get_simple_labels()])
    return X, Y


def _pad_sequences(Xs, Ys, max_len):
    newXs = pad_sequences(Xs, padding='post', dtype=np.int8, maxlen=max_len, value=MASK_VALUE)
    newYs = pad_sequences(Ys, padding='post', maxlen=max_len, value=MASK_VALUE)
    return newXs, newYs


def _prepare_words(words, max_len):
    result_x, result_y = [], []
    print("Preparing words")
    for i, word in enumerate(words):
        word_x, word_answer = _get_parse_repr(word)
        result_x.append(word_x)
        result_y.append(word_answer)
        if i % 1000 == 0:
            print("Prepared", i)

    return _pad_sequences(result_x, result_y, max_len)


class MorphemModel(object):
    def __init__(self, dropout, layers, models_number, epochs, validation_split, window_sizes, max_len):
        self.dropout = dropout
        self.layers = layers
        self.models_number = models_number
        self.epochs = epochs
        self.validation_split = validation_split
        self.window_sizes = window_sizes
        self.activation = "softmax"
        self.optimizer = "adam"
        self.models = []
        self.max_len = max_len

    def _transform_classification(self, parse):
        parts = []
        current_part = [parse[0]]
        for num, letter in enumerate(parse[1:]):
            index = num + 1
            if letter == 'SUFF' and parse[index - 1] == 'B-SUFF':
                current_part.append(letter)
            elif letter == 'PREF' and parse[index - 1] == 'B-PREF':
                current_part.append(letter)
            elif letter == 'ROOT' and parse[index - 1] == 'B-ROOT':
                current_part.append(letter)
            elif letter != parse[index - 1] or letter.startswith('B-'):
                parts.append(current_part)
                current_part = [letter]
            else:
                current_part.append(letter)
        if current_part:
            parts.append(current_part)

        for part in parts:
            if part[0] == 'B-PREF':
                part[0] = 'PREF'
            if part[0] == 'B-SUFF':
                part[0] = 'SUFF'
            if part[0] == 'B-ROOT':
                part[0] = 'ROOT'
            if len(part) == 1:
                part[0] = 'S-' + part[0]
            else:
                part[0] = 'B-' + part[0]
                part[-1] = 'E-' + part[-1]
                for num, letter in enumerate(part[1:-1]):
                    part[num+1] = 'M-' + letter
        result = []
        for part in parts:
            result += part
        return result

    def _build_model(self, input_maxlen):
        inp = Input(shape=(input_maxlen, len(LETTERS) + 1 + 1 + len(SPEECH_PARTS)))
        inputs = [inp]
        do = None

        conv_outputs = []
        for drop, units, window_size in zip(self.dropout, self.layers, self.window_sizes):
            conv = Conv1D(units, window_size, padding="same", activation='relu')(inp)
            do = Dropout(drop)(conv)
            inp = do
            conv_outputs.append(do)

        concat = conv_outputs[-1]

        outputs = [TimeDistributed(
            Dense(len(PARTS_MAPPING), activation=self.activation))(concat)]

        self.models.append(Model(inputs, outputs=outputs))
        self.models[-1].compile(loss='categorical_crossentropy',
                                optimizer=self.optimizer, metrics=['acc'])

        print(self.models[-1].summary())

    def train(self, words, validation):
        (x, y,) = _prepare_words(words, self.max_len)
        (val_x, val_y) = _prepare_words(validation, self.max_len)
        for i in range(self.models_number):
            self._build_model(self.max_len)
        es = EarlyStopping(monitor='val_acc', patience=8, verbose=1)
        self.models[-1].fit(x, y, epochs=self.epochs, verbose=2,
                            callbacks=[es], validation_data=(val_x, val_y), batch_size=8192)
        self.models[-1].save("keras_morphem_model_{}.h5".format(int(time.time())))

    def load(self, path):
        self.models.append(keras.models.load_model(path))

    def classify(self, words):
        print("Total models:", len(self.models))
        (x, _,) = _prepare_words(words, self.max_len)
        pred = self.models[-1].predict(x)
        pred_class = pred.argmax(axis=-1)
        reverse_mapping = {v: k for k, v in PARTS_MAPPING.items()}
        result = []
        for i, word in enumerate(words):
            cutted_prediction = pred_class[i][:len(word.get_word())]
            raw_parse = [reverse_mapping[int(num)] for num in cutted_prediction]
            parse = self._transform_classification(raw_parse)
            result.append(parse)
        return result


if __name__ == "__main__":
    parser = ArgumentParser(description="Train and evaluate model for morphem split")
    parser.add_argument("--model-path", help="Path to trained model with .h5 extension")
    parser.add_argument("--train-set", help="Path to train set")
    parser.add_argument("--val-set", help="Path to validation set")
    parser.add_argument("--test-lemma-set", help="Path to lemma test set", required=True)
    parser.add_argument("--test-lexeme-set", help="Path to lexeme test set")
    parser.add_argument("--verbose", action='store_true', help="Verbose information about errors")

    args = parser.parse_args()

    if args.model_path and (args.train_set or args.val_set):
        raise Exception("If --model-path specified, than only testing of model is possible")
    if not args.model_path and not args.train_set:
        raise Exception("One of --model-path or --train-set must be specified")

    train_part = []
    counter = 0
    max_len = 0
    if args.train_set:
        with open(args.train_set, 'r') as data:
            for num, line in enumerate(data):
                counter += 1
                word = parse_word(line.strip())
                if word is None:
                    continue
                train_part.append(word)
                max_len = max(max_len, len(train_part[-1]))
                if counter % 1000 == 0:
                    print("Loaded", counter, "train words")

    validation_part = []
    if args.val_set:
        with open(args.val_set, 'r') as data:
            for num, line in enumerate(data):
                counter += 1
                word = parse_word(line.strip())
                if word is None:
                    continue
                validation_part.append(word)
                max_len = max(max_len, len(validation_part[-1]))
                if counter % 1000 == 0:
                    print("Loaded", counter, "train words")

    test_lexeme_part = []
    if args.test_lexeme_set:
        with open(args.test_lexeme_set, 'r') as data:
            for num, line in enumerate(data):
                counter += 1
                word = parse_word(line.strip())
                if word is None:
                    continue
                test_lexeme_part.append(word)
                max_len = max(max_len, len(test_lexeme_part[-1]))
                if counter % 1000 == 0:
                    print("Loaded", counter, "test words")

    test_lemma_part = []
    if args.test_lemma_set:
        with open(args.test_lemma_set, 'r') as data:
            for num, line in enumerate(data):
                counter += 1
                word = parse_word(line.strip())
                if word is None:
                    continue
                test_lemma_part.append(word)
                max_len = max(max_len, len(test_lemma_part[-1]))
                if counter % 1000 == 0:
                    print("Loaded", counter, "test words")

    print("Maxlen", max_len)
    model = MorphemModel([0.4, 0.4, 0.4], [512, 512, 512], 1, 60, 0.1, [5, 5, 5], max_len)
    if train_part:
        print("Training model")
        model.train(train_part, validation_part)
    else:
        print("Loading model")
        model.load(args.model_path)

    if test_lexeme_part:
        print("Lexeme result:")
        result_lexeme = model.classify(test_lexeme_part)
        print(measure_quality(result_lexeme, [w.get_labels() for w in test_lexeme_part], test_lexeme_part, args.verbose))

    if test_lemma_part:
        print("Lemma result:")
        result_lemma = model.classify(test_lemma_part)
        print(measure_quality(result_lemma , [w.get_labels() for w in test_lemma_part], test_lemma_part, args.verbose))
