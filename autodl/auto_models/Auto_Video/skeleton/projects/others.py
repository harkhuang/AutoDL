# -*- coding: utf-8 -*-
from __future__ import absolute_import
import sys
import os
import logging
from functools import reduce

import tensorflow as tf
import torchvision as tv
import numpy as np


def get_logger(name, stream=sys.stderr, file_path='debug.log'):
    formatter = logging.Formatter(fmt='[%(asctime)s %(levelname)s %(filename)s] %(message)s')

    handler = logging.StreamHandler(stream)
    # handler = logging.FileHandler(file_path)
    handler.setFormatter(formatter)

    logger = logging.getLogger(name)
    level = logging.INFO if os.environ.get('LOG_LEVEL', 'INFO') == 'INFO' else logging.DEBUG
    logger.setLevel(level)
    logger.addHandler(handler)
    return logger


LOGGER = get_logger(__name__)

def get_tf_resize(height, width, times=1, min_value=0.0, max_value=1.0):
    def preprocessor(tensor):
        in_times, in_height, in_width, in_channels = tensor.get_shape()

        if width == in_width and height == in_height:
            pass
        else:
            tensor = tf.image.resize_images(tensor, (height, width), method=tf.image.ResizeMethod.BICUBIC)

        if times != in_times or times > 1:
            tensor = tf.reshape(tensor, [-1, height * width, in_channels])
            tensor = tf.image.resize_images(tensor, (times, height * width),
                                            method=tf.image.ResizeMethod.BICUBIC)
            tensor = tf.reshape(tensor, [times, height, width, in_channels])
        if times == 1:
            tensor = tensor[int(times // 2)]

        delta = max_value - min_value
        if delta < 0.9 or delta > 1.1 or min_value < -0.1 or min_value > 0.1:
            tensor = (tensor - min_value) / delta
        return tensor

    return preprocessor


def get_tf_to_tensor(is_random_flip=True):
    def preprocessor(tensor):
        if is_random_flip:
            tensor = tf.image.random_flip_left_right(tensor)
        dims = len(tensor.shape)
        if dims == 3:
            tensor = tf.transpose(tensor, perm=[2, 0, 1])
        elif dims == 4:
            tensor = tf.transpose(tensor, perm=[3, 0, 1, 2])
        return tensor

    return preprocessor


def NBAC(logits, labels):
    if logits.device != labels.device:
        labels = labels.to(device=logits.device)

    positive_mask = labels > 0
    negative_mask = labels < 1

    tpr = (logits * labels).sum() / positive_mask.sum()
    tnr = ((1 - logits) * (1 - labels)).sum() / negative_mask.sum()
    return tpr, tnr, (tpr + tnr - 1)


def tiedrank(a):
    ''' Return the ranks (with base 1) of a list resolving ties by averaging.
     This works for numpy arrays.'''
    m = len(a)
    # Sort a in ascending order (sa=sorted vals, i=indices)
    i = a.argsort()
    sa = a[i]
    # Find unique values
    uval = np.unique(a)
    # Test whether there are ties
    R = np.arange(m, dtype=float) + 1  # Ranks with base 1
    if len(uval) != m:
        # Average the ranks for the ties
        oldval = sa[0]
        newval = sa[0]
        k0 = 0
        for k in range(1, m):
            newval = sa[k]
            if newval == oldval:
                # moving average
                R[k0:k + 1] = R[k - 1] * (k - k0) / (k - k0 + 1) + R[k] / (k - k0 + 1)
            else:
                k0 = k;
                oldval = newval
    # Invert the index
    S = np.empty(m)
    S[i] = R
    return S


def mvmean(R, axis=0):
    ''' Moving average to avoid rounding errors. A bit slow, but...
    Computes the mean along the given axis, except if this is a vector, in which case the mean is returned.
    Does NOT flatten.'''
    if len(R.shape) == 0: return R
    average = lambda x: reduce(lambda i, j: (0, (j[0] / (j[0] + 1.)) * i[1] + (1. / (j[0] + 1)) * j[1]), enumerate(x))[
        1]
    R = np.array(R)
    if len(R.shape) == 1: return average(R)
    if axis == 1:
        return np.array(map(average, R))
    else:
        return np.array(map(average, R.transpose()))


def get_valid_columns(solution):
    """Get a list of column indices for which the column has more than one class.
    This is necessary when computing BAC or AUC which involves true positive and
    true negative in the denominator. When some class is missing, these scores
    don't make sense (or you have to add an epsilon to remedy the situation).

    Args:
    solution: array, a matrix of binary entries, of shape
      (num_examples, num_features)
    Returns:
    valid_columns: a list of indices for which the column has more than one
      class.
    """
    num_examples = solution.shape[0]
    col_sum = np.sum(solution, axis=0)
    valid_columns = np.where(1 - np.isclose(col_sum, 0) -
                             np.isclose(col_sum, num_examples))[0]
    return valid_columns


def AUC(logits, labels):
    logits = logits.detach().float().cpu().numpy()
    labels = labels.detach().float().cpu().numpy()

    valid_columns = get_valid_columns(labels)

    logits = logits[:, valid_columns].copy()
    labels = labels[:, valid_columns].copy()

    label_num = labels.shape[1]
    if label_num == 0:
        return 0.0

    auc = np.empty(label_num)
    for k in range(label_num):
        r_ = tiedrank(logits[:, k])
        s_ = labels[:, k]

        npos = sum(s_ == 1)
        nneg = sum(s_ < 1)
        auc[k] = (sum(r_[s_ == 1]) - npos * (npos + 1) / 2) / (nneg * npos)

    return 2 * mvmean(auc) - 1