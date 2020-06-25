# dataloader add 3.0 scale
# dataloader add filer text
import sys, os
base_path = os.path.dirname(os.path.dirname(
                            os.path.abspath(__file__)))
sys.path.append(base_path)
import numpy as np
from PIL import Image
from torch.utils import data
import util
import cv2
import random
import torchvision.transforms as transforms
import torch
import pyclipper
import Polygon as plg
from bresenham import bresenham
from math import exp
import time

from tqdm import tqdm
import glob
import json

ic15_root_dir = './data/ICDAR2015/Challenge4/'
ic15_train_data_dir = ic15_root_dir + 'ch4_training_images/'
ic15_train_gt_dir = ic15_root_dir + 'ch4_training_localization_transcription_gt/'
ic15_test_data_dir = ic15_root_dir + 'ch4_test_images/'
ic15_test_gt_dir = ic15_root_dir + 'ch4_test_localization_transcription_gt/'

random.seed(123456)

from albumentations import (
    Compose, RGBShift, RandomBrightness, RandomContrast,
    HueSaturationValue, ChannelShuffle, CLAHE,
    RandomContrast, Blur, ToGray, JpegCompression,
    CoarseDropout  
)

def augument():
    augm = Compose([
        RGBShift(),
        RandomBrightness(),
        RandomContrast(),
        HueSaturationValue(p=0.2),
        ChannelShuffle(),
        CLAHE(),
        Blur(),
        ToGray(),
        CoarseDropout()
    ],
    p=0.5)
    return augm
    
def get_img(img_path):
    try:
        img = cv2.imread(img_path)
        img = img[:, :, [2, 1, 0]]
    except Exception as e:
        img = np.zeros((640, 640, 3))
    return img

def get_bboxes(img, gt_path, keywords=None):
    h, w = img.shape[0:2]
    lines = util.io.read_lines(gt_path)
    bboxes = []
    tags = []
    texts = [] 
    for line in lines:
        line = util.str.remove_all(line, '\xef\xbb\xbf')
        if len(line) < 1:
            continue
        gt = util.str.split(line, ',')
        gt = [i.strip('\ufeff').strip('\xef\xbb\xbf').strip('\n') for i in gt]
        if len(gt) < 5:
            continue

        if gt[-1][0] == '#':
            tags.append(False)
        else:
            tags.append(True)
        if keywords is not None:
            text_ids = list(map(lambda x: keywords[x], gt[-1]))
            print(gt[-1], text_ids)
            texts.append(text_ids)
        try:
            box = [int(eval(gt[i])) for i in range(8)]
        except:
            print(box)
        box = np.asarray(box)
        bboxes.append(box)

    return np.array(bboxes, dtype=np.float32).reshape((-1, 4, 2)), tags, texts

import random
def crop_area_v2(im, polys, tags, texts=None, crop_background=False, min_crop_side_ratio=0.24, max_tries=50):
    '''
    make random crop from the input image
    :param im:
    :param polys:
    :param tags:
    :param crop_background:
    :param max_tries:
    :return:
    '''
    h, w, _ = im.shape
    pad_h = h//10
    pad_w = w//10
    h_array = np.zeros((h + pad_h*2), dtype=np.int32)
    w_array = np.zeros((w + pad_w*2), dtype=np.int32)

    for poly in polys:
        poly = np.round(poly, decimals=0).astype(np.int32)
        minx = np.min(poly[:, 0])
        maxx = np.max(poly[:, 0])
        w_array[minx+pad_w:maxx+pad_w] = 1
        miny = np.min(poly[:, 1])
        maxy = np.max(poly[:, 1])
        h_array[miny+pad_h:maxy+pad_h] = 1
    # ensure the cropped area not across a text
    h_axis = np.where(h_array == 0)[0]
    w_axis = np.where(w_array == 0)[0]
    if len(h_axis) == 0 or len(w_axis) == 0:
        return im, polys, tags, texts

    for i in range(max_tries):
        xx = np.random.choice(w_axis, size=2)
        xmin = np.min(xx) - pad_w
        xmax = np.max(xx) - pad_w
        xmin = np.clip(xmin, 0, w-1)
        xmax = np.clip(xmax, 0, w-1)
        yy = np.random.choice(h_axis, size=2)
        ymin = np.min(yy) - pad_h
        ymax = np.max(yy) - pad_h
        ymin = np.clip(ymin, 0, h-1)
        ymax = np.clip(ymax, 0, h-1)
        if xmax - xmin < min_crop_side_ratio*w or ymax - ymin < min_crop_side_ratio*h:
            # area too small
            continue
        if polys.shape[0] != 0:
            poly_axis_in_area = (polys[:, :, 0] >= xmin) & (polys[:, :, 0] <= xmax) \
                                & (polys[:, :, 1] >= ymin) & (polys[:, :, 1] <= ymax)
            selected_polys = np.where(np.sum(poly_axis_in_area, axis=1) == 4)[0]
        else:
            selected_polys = []
        if len(selected_polys) == 0:
            # no text in this area
            if crop_background:
                texts = list(map(lambda x: texts[x], selected_polys))
                return im[ymin:ymax+1, xmin:xmax+1, :], polys[selected_polys], tags[selected_polys], texts
            else:
                continue
        im = im[ymin:ymax+1, xmin:xmax+1, :]
        polys = polys[selected_polys]
        tags = tags[selected_polys]

        if texts is not None:
            texts = list(map(lambda x: texts[x], selected_polys))

        polys[:, :, 0] -= xmin
        polys[:, :, 1] -= ymin
        return im, polys, tags, texts

    return im, polys, tags

def random_horizontal_flip(imgs):
    if random.random() < 0.5:
        for i in range(len(imgs)):
            imgs[i] = np.flip(imgs[i], axis=1).copy()
    return imgs

def random_rotate(imgs):
    max_angle = 90
    angle = random.random() * 2 * max_angle - max_angle
    for i in range(len(imgs)):
        img = imgs[i]
        w, h = img.shape[:2]
        rotation_matrix = cv2.getRotationMatrix2D((h / 2, w / 2), angle, 1)
        img_rotation = cv2.warpAffine(img, rotation_matrix, (h, w))
        imgs[i] = img_rotation
    return imgs

def scale(img, long_size=2240):
    h, w = img.shape[0:2]
    scale = long_size * 1.0 / max(h, w)
    img = cv2.resize(img, dsize=None, fx=scale, fy=scale)
    return img

def random_scale(img, min_size):
    h, w = img.shape[0:2]
    if max(h, w) > 1280:
        scale = 1280.0 / max(h, w)
        img = cv2.resize(img, dsize=None, fx=scale, fy=scale)

    h, w = img.shape[0:2]
    random_scale = np.array([0.5, 1.0, 2.0, 3.0])
    scale = np.random.choice(random_scale)
    if min(h, w) * scale <= min_size:
        scale = (min_size + 10) * 1.0 / min(h, w)
    img = cv2.resize(img, dsize=None, fx=scale, fy=scale)
    return img

def random_crop(imgs, img_size):
    h, w = imgs[0].shape[0:2]
    th, tw = img_size
    if w == tw and h == th:
        return imgs
    
    if random.random() > 3.0 / 8.0 and np.max(imgs[1]) > 0:
        tl = np.min(np.where(imgs[1] > 0), axis = 1) - img_size
        tl[tl < 0] = 0
        br = np.max(np.where(imgs[1] > 0), axis = 1) - img_size
        br[br < 0] = 0
        br[0] = min(br[0], h - th)
        br[1] = min(br[1], w - tw)
        
        i = random.randint(tl[0], br[0])
        j = random.randint(tl[1], br[1])
    else:
        i = random.randint(0, h - th)
        j = random.randint(0, w - tw)
    
    # return i, j, th, tw
    for idx in range(len(imgs)):
        if len(imgs[idx].shape) == 3:
            imgs[idx] = imgs[idx][i:i + th, j:j + tw, :]
        else:
            imgs[idx] = imgs[idx][i:i + th, j:j + tw]

    resized_ims = []
    new_h, new_w, _ = imgs[0].shape
    max_h_w_i = np.max([new_h, new_w, th, tw])

    for ims in imgs:
        if len(ims.shape) > 2:
            im_padded = np.zeros((max_h_w_i, max_h_w_i, 3), dtype=np.uint8)
            im_padded[:new_h, :new_w, :] = ims.copy()
        else:
            im_padded = np.zeros((max_h_w_i, max_h_w_i), dtype=np.uint8)
            im_padded[:new_h, :new_w] = ims.copy()
        
        img = cv2.resize(im_padded, dsize=(th, tw))
        resized_ims.append(img)

    return resized_ims

def dist(a, b):
    return np.sqrt(np.sum((a - b) ** 2))
    
def polygon_area(poly):
    '''
    compute area of a polygon
    :param poly:
    :return:
    '''
    edge = [
        (poly[1][0] - poly[0][0]) * (poly[1][1] + poly[0][1]),
        (poly[2][0] - poly[1][0]) * (poly[2][1] + poly[1][1]),
        (poly[3][0] - poly[2][0]) * (poly[3][1] + poly[2][1]),
        (poly[0][0] - poly[3][0]) * (poly[0][1] + poly[3][1])
    ]
    return np.sum(edge)/2.

def is_polygon(poly):
    for i in range(3):
        p0 = poly[i]
        p1 = poly[(i + 1) % 4]
        p2 = poly[(i + 2) % 4]
        # 判断是否有两个点重叠
        if p0[0] == p1[0] and p1[1] == p0[1]:
            return False
        if p0[0] == p2[0] and p2[1] == p0[1]:
            return False
        if p1[0] == p2[0] and p1[1] == p2[1]:
            return False
        # 判断是否有3个点在一条直线上
        if p0[0] == p1[0]:
            if p1[0] == p2[0]:
                return False
        else:
            if p1[0] != p2[0]:
                k1 = (p1[1] - p0[1]) / (p1[0] - p0[0])
                k2 = (p2[1] - p1[1]) / (p2[0] - p1[0])
                if abs(k1 - k2) < 1e-6:
                    return False
            else:
                if p1[1] == p2[1]:
                    return False
    return True

def check_and_validate_polys(polys, tags, xxx_todo_changeme, texts=None):
    '''
    check so that the text poly is in the same direction,
    and also filter some invalid polygons
    :param polys:
    :param tags:
    :return:
    '''
    (h, w) = xxx_todo_changeme
    
    if polys.shape[0] == 0:
        return polys, np.array(tags), texts

    polys[:, :, 0] = np.clip(polys[:, :, 0], 0, w-1)
    polys[:, :, 1] = np.clip(polys[:, :, 1], 0, h-1)

    validated_polys = []
    validated_tags = []
    validated_texts = []

    for i, (poly, tag) in enumerate(zip(polys, tags)):
        p_area = polygon_area(poly)
        if abs(p_area) < 1:
            continue

        if not is_polygon(poly):
            continue

        if p_area > 0:
            poly = poly[(0, 3, 2, 1), :]

        validated_polys.append(poly)
        validated_tags.append(tag)
        if texts is not None:
            validated_texts.append(texts[i])

    return np.array(validated_polys), np.array(validated_tags), validated_texts

def shrink_poly(poly, r):
    '''
    fit a poly inside the origin poly, maybe bugs here...
    used for generate the score map
    :param poly: the text poly
    :param r: r in the paper
    :return: the shrinked poly
    '''
    # shrink ratio
    R = 0.1
    # find the longer pair
    if np.linalg.norm(poly[0] - poly[1]) + np.linalg.norm(poly[2] - poly[3]) > \
                    np.linalg.norm(poly[0] - poly[3]) + np.linalg.norm(poly[1] - poly[2]):
        # first move (p0, p1), (p2, p3), then (p0, p3), (p1, p2)
        ## p0, p1
        theta = np.arctan2((poly[1][1] - poly[0][1]), (poly[1][0] - poly[0][0]))
        poly[0][0] += R * r[0] * np.cos(theta)
        poly[0][1] += R * r[0] * np.sin(theta)
        poly[1][0] -= R * r[1] * np.cos(theta)
        poly[1][1] -= R * r[1] * np.sin(theta)
        ## p2, p3
        theta = np.arctan2((poly[2][1] - poly[3][1]), (poly[2][0] - poly[3][0]))
        poly[3][0] += R * r[3] * np.cos(theta)
        poly[3][1] += R * r[3] * np.sin(theta)
        poly[2][0] -= R * r[2] * np.cos(theta)
        poly[2][1] -= R * r[2] * np.sin(theta)
        ## p0, p3
        theta = np.arctan2((poly[3][0] - poly[0][0]), (poly[3][1] - poly[0][1]))
        poly[0][0] += R * r[0] * np.sin(theta)
        poly[0][1] += R * r[0] * np.cos(theta)
        poly[3][0] -= R * r[3] * np.sin(theta)
        poly[3][1] -= R * r[3] * np.cos(theta)
        ## p1, p2
        theta = np.arctan2((poly[2][0] - poly[1][0]), (poly[2][1] - poly[1][1]))
        poly[1][0] += R * r[1] * np.sin(theta)
        poly[1][1] += R * r[1] * np.cos(theta)
        poly[2][0] -= R * r[2] * np.sin(theta)
        poly[2][1] -= R * r[2] * np.cos(theta)
    else:
        ## p0, p3
        # print poly
        theta = np.arctan2((poly[3][0] - poly[0][0]), (poly[3][1] - poly[0][1]))
        poly[0][0] += R * r[0] * np.sin(theta)
        poly[0][1] += R * r[0] * np.cos(theta)
        poly[3][0] -= R * r[3] * np.sin(theta)
        poly[3][1] -= R * r[3] * np.cos(theta)
        ## p1, p2
        theta = np.arctan2((poly[2][0] - poly[1][0]), (poly[2][1] - poly[1][1]))
        poly[1][0] += R * r[1] * np.sin(theta)
        poly[1][1] += R * r[1] * np.cos(theta)
        poly[2][0] -= R * r[2] * np.sin(theta)
        poly[2][1] -= R * r[2] * np.cos(theta)
        ## p0, p1
        theta = np.arctan2((poly[1][1] - poly[0][1]), (poly[1][0] - poly[0][0]))
        poly[0][0] += R * r[0] * np.cos(theta)
        poly[0][1] += R * r[0] * np.sin(theta)
        poly[1][0] -= R * r[1] * np.cos(theta)
        poly[1][1] -= R * r[1] * np.sin(theta)
        ## p2, p3
        theta = np.arctan2((poly[2][1] - poly[3][1]), (poly[2][0] - poly[3][0]))
        poly[3][0] += R * r[3] * np.cos(theta)
        poly[3][1] += R * r[3] * np.sin(theta)
        poly[2][0] -= R * r[2] * np.cos(theta)
        poly[2][1] -= R * r[2] * np.sin(theta)
    return poly
      

# SANet
def gaussian_2d(radius=None):
    # sigma = radius/3.

    # sigma = 10
    spread = 3.7
    sigma = radius * 1.0 / spread
    # radius = int(spread * sigma)
    gaussian_heatmap = np.zeros([2 * radius, 2 * radius], dtype=np.float32)
    for i in range(2 * radius):
            for j in range(2 * radius):
                tmp_pi = 1. / 2. / np.pi / (sigma ** 2)
                gaussian_heatmap[i, j] = tmp_pi * np.exp(-1 / 2 * ((i - radius - 0.5) ** 2 + (j - radius - 0.5) ** 2) / (sigma ** 2))

    gaussian_heatmap = gaussian_heatmap / (np.max(gaussian_heatmap))
    return gaussian_heatmap

def find_long_edges(points, bottoms):
    b1_start, b1_end = bottoms[0]
    b2_start, b2_end = bottoms[1]
    n_pts = len(points)
    i = (b1_end + 1) % n_pts
    long_edge_1 = []

    while (i % n_pts != b2_end):
        start = (i - 1) % n_pts
        end = i % n_pts
        long_edge_1.append((start, end))
        i = (i + 1) % n_pts

    i = (b2_end + 1) % n_pts
    long_edge_2 = []
    while (i % n_pts != b1_end):
        start = (i - 1) % n_pts
        end = i % n_pts
        long_edge_2.append((start, end))
        i = (i + 1) % n_pts
    return long_edge_1, long_edge_2
    
def norm2(x, axis=None):
    if axis:
        return np.sqrt(np.sum(x ** 2, axis=axis))
    return np.sqrt(np.sum(x ** 2))

def find_bottom(pts):

    if len(pts) > 4:
        e = np.concatenate([pts, pts[:3]])
        candidate = []
        for i in range(1, len(pts) + 1):
            v_prev = e[i] - e[i - 1]
            v_next = e[i + 2] - e[i + 1]
            if cos(v_prev, v_next) < -0.7:
                candidate.append((i % len(pts), (i + 1) % len(pts), norm2(e[i] - e[i + 1])))

        if len(candidate) != 2 or candidate[0][0] == candidate[1][1] or candidate[0][1] == candidate[1][0]:
            # if candidate number < 2, or two bottom are joined, select 2 farthest edge
            mid_list = []
            for i in range(len(pts)):
                mid_point = (e[i] + e[(i + 1) % len(pts)]) / 2
                mid_list.append((i, (i + 1) % len(pts), mid_point))

            dist_list = []
            for i in range(len(pts)):
                for j in range(len(pts)):
                    s1, e1, mid1 = mid_list[i]
                    s2, e2, mid2 = mid_list[j]
                    dist = norm2(mid1 - mid2)
                    dist_list.append((s1, e1, s2, e2, dist))
            bottom_idx = np.argsort([dist for s1, e1, s2, e2, dist in dist_list])[-2:]
            bottoms = [dist_list[bottom_idx[0]][:2], dist_list[bottom_idx[1]][:2]]
        else:
            bottoms = [candidate[0][:2], candidate[1][:2]]

    else:
        d1 = norm2(pts[1] - pts[0]) + norm2(pts[2] - pts[3])
        d2 = norm2(pts[2] - pts[1]) + norm2(pts[0] - pts[3])
        bottoms = [(0, 1), (2, 3)] if d1 < d2 else [(1, 2), (3, 0)]
    assert len(bottoms) == 2, 'fewer than 2 bottoms'
    return bottoms


def norm_distance(pt1, pt2):
    return np.linalg.norm(np.array(pt1) - np.array(pt2))

def four_point_transform(image, pts):
    max_x, max_y = np.max(pts[:, 0]).astype(np.int32), np.max(pts[:, 1]).astype(np.int32)

    dst = np.array([
        [0, 0],
        [image.shape[1] - 1, 0],
        [image.shape[1] - 1, image.shape[0] - 1],
        [0, image.shape[0] - 1]], dtype="float32")

    M = cv2.getPerspectiveTransform(dst, pts)
    warped = cv2.warpPerspective(image, M, (max_x, max_y))
    return warped

def point2fixedAxis(point, fixedAxis):
    """
    计算垂足
    """
    vector1 = point - fixedAxis[0]
    vector2 = point - fixedAxis[1]
    vector3 = fixedAxis[1] - fixedAxis[0]

    k = np.dot(fixedAxis[0] - point, fixedAxis[1] - fixedAxis[0])
    k /= -np.square(np.linalg.norm(vector3))
    dropFoot = k * (vector3) + fixedAxis[0]

    d = np.linalg.norm(np.cross(vector1, vector2))/np.linalg.norm(vector3)
    return d, dropFoot

def sort_rectangle(poly):
    # sort the four coordinates of the polygon, points in poly should be sorted clockwise
    # First find the lowest point
    p_lowest = np.argmax(poly[:, 1])
    if np.count_nonzero(poly[:, 1] == poly[p_lowest, 1]) == 2:
        # 底边平行于X轴, 那么p0为左上角 - if the bottom line is parallel to x-axis, then p0 must be the upper-left corner
        p0_index = np.argmin(np.sum(poly, axis=1))
        p1_index = (p0_index + 1) % 4
        p2_index = (p0_index + 2) % 4
        p3_index = (p0_index + 3) % 4
        return poly[[p0_index, p1_index, p2_index, p3_index]], 0.
    else:
        # 找到最低点右边的点 - find the point that sits right to the lowest point
        p_lowest_right = (p_lowest - 1) % 4
        p_lowest_left = (p_lowest + 1) % 4
        angle = np.arctan(-(poly[p_lowest][1] - poly[p_lowest_right][1])/(poly[p_lowest][0] - poly[p_lowest_right][0]))
        # assert angle > 0
        if angle <= 0:
            return poly, angle
        
        if angle/np.pi * 180 > 45:
            # 这个点为p2 - this point is p2
            p2_index = p_lowest
            p1_index = (p2_index - 1) % 4
            p0_index = (p2_index - 2) % 4
            p3_index = (p2_index + 1) % 4
            return poly[[p0_index, p1_index, p2_index, p3_index]], -(np.pi/2 - angle)
        else:
            # 这个点为p3 - this point is p3
            p3_index = p_lowest
            p0_index = (p3_index + 1) % 4
            p1_index = (p3_index + 2) % 4
            p2_index = (p3_index + 3) % 4
            return poly[[p0_index, p1_index, p2_index, p3_index]], angle

def generate_gaussian_target(polys, h, w, training_mask, texts=None, keywords=None):
    """
    Args:
        polys: [4, 2]
    """
    m = 3
    heat_map = np.zeros((h, w))
    R_scales = [0, 10, 50, 100, 150, 200]
    poly_mask = np.zeros((h, w))
    border_map = np.zeros((h, w))
    geometry_map = np.zeros((4, h, w), np.float32)
    densebox = np.zeros((8, h, w), np.float32)
    densebox_anchor = np.indices((h, w))[::-1].astype(np.float32)
    mask = np.zeros((h, w))

    num_keywords = len(keywords) if keywords is not None else 0

    phoc_map = np.zeros((num_keywords, h, w))

    vector = np.zeros((num_keywords,))
    
    for poly_idx, poly in enumerate(polys):
        
        r = [None, None, None, None]
        edge = []
        for i in range(4):
            edge.append(np.linalg.norm(poly[i] - poly[(i + 1) % 4]))
            r[i] = min(np.linalg.norm(poly[i] - poly[(i + 1) % 4]),
                       np.linalg.norm(poly[i] - poly[(i - 1) % 4]))

        # score map
        shrinked_poly = shrink_poly(poly.copy(), r).astype(np.int32)[np.newaxis, :, :]
        cv2.fillPoly(border_map, [poly], 1)
        cv2.fillPoly(border_map, shrinked_poly, 0)
        cv2.fillPoly(mask, poly.astype(np.int32)[np.newaxis, :, :], 1)

        if keywords is not None:
            # phmask = np.zeros((h, w))
            # cv2.fillPoly(phmask, poly.astype(np.int32)[np.newaxis, :, :], 1)
            curr_text = texts[poly_idx]
            for idx in curr_text:
                cv2.fillPoly(phoc_map[idx], poly.astype(np.int32)[np.newaxis, :, :], 1)
            # phoc_map[curr_text] = phoc_map[curr_text] + np.concatenate([phmask[np.newaxis, :, :]] * len(curr_text), axis=0)

        poly_h = min(np.linalg.norm(poly[0] - poly[3]), np.linalg.norm(poly[1] - poly[2]))
        poly_w = min(np.linalg.norm(poly[0] - poly[1]), np.linalg.norm(poly[2] - poly[3]))
        
        if min(poly_h, poly_w) < 8:
            cv2.fillPoly(training_mask, poly.astype(np.int32)[np.newaxis, :, :], 0)

        poly, angle = sort_rectangle(poly)
        for i in range(0, 4):
            for j in range(0, 2):
                cv2.fillConvexPoly(densebox[i * 2 + j], shrinked_poly, float(poly[i][j]))


        if min(edge) < 1:
            continue
            
        ratio = max(edge) * 1.0 / min(edge) * 1.0

            
        if ratio < 1.5:
            poly = polys[poly_idx]

        else:
            bottom = find_bottom(poly)
            e1, e2 = find_long_edges(poly, bottom)
            id0, id1 = e1[0]
            id2, id3 = e2[0]
            poly = np.array(poly)[[id0, id1, id2, id3]]
        
        x0, y0 = poly[0]
        x1, y1 = poly[1]
        x2, y2 = poly[2]
        x3, y3 = poly[3]

        topside_pts = list(bresenham(x0, y0, x1, y1))
        bottomside_pts = list(bresenham(x2, y2, x3, y3))
        # stride = len(topside_pts) // m
        stride = 1
        top_pts = topside_pts
        bot_pts = bottomside_pts[::-1]
        center_pts = []
        radius = []

        for tp_pts, bt_pts in zip(top_pts, bot_pts):
            tx, ty = tp_pts
            bx, by = bt_pts
            cx = (tx + bx) / 2.
            cy = (ty + by) / 2.
            ce_pt = (cx, cy)
            center_pts.append(ce_pt)
            radius_p = (norm_distance(tp_pts, ce_pt) + norm_distance(bt_pts, ce_pt)) / 2.
            radius.append(radius_p)
        # print("process_poly:{}, get_line:{}, cal_radius:{}".format(t2-t1, t3-t2, t4-t3))
        R = int(radius[0])
        
        if R==0:
            continue
        index = 0
        R_map = R
#        R_scales = [0, 10, 50, 100, 150, 200]
        if R > R_scales[0] and R <= R_scales[1]:
            R_map = R_scales[1]
            index = 0
        elif R > R_scales[1] and R <= R_scales[2]:
            R_map = R_scales[2]//2
            index = 1
        elif R > R_scales[2] and R <= R_scales[3]:
            R_map = 30
            index = 2
        elif R > R_scales[3] and R <= R_scales[4]:
            R_map = 90
            index = 2
        elif R > R_scales[4] and R <= R_scales[5]:
            R_map = 120
            index = 2
        elif R > R_scales[5]:
            R_map = 150
            
        tmp_gaussian_map = gaussian_2d(R_map)
            
        char_box = np.array([top_pts[0], top_pts[min(R*2, len(top_pts)-1)], bot_pts[min(R*2, len(bot_pts)-1)], bot_pts[0]]).astype(np.float32)
        top_left = np.array([np.min(char_box[:, 0]), np.min(char_box[:, 1])]).astype(np.int32)
        char_box -= top_left[None, :]
        
        tmp_gaussian_map = four_point_transform(tmp_gaussian_map, char_box)
        gm_h, gm_w = list(map(lambda x:int(x/2.), tmp_gaussian_map.shape))
        Rs = int(0.3 * R) if ratio > 1.5 else int(0.2 * R)
        
        strides = min(int(len(radius) * 0.2), int(R))
        for i, Rk in enumerate(radius[strides:-strides]):
            cpts = center_pts[strides + i]
            cx, cy = list(map(int, cpts))
            cx1, cy1 = list(map(int, center_pts[strides + i + 1]))
            cent_pts = list(bresenham(cx, cy, cx1, cy1))
            for ct_pt in cent_pts:
                x, y = list(map(int, ct_pt))
                y0 = y - min(R, gm_h)
                y1 = y + min(R, gm_h)
                x0 = x - min(R, gm_w)
                x1 = x + min(R, gm_w)

                y0r = min(R, gm_h) if y0 >= 0 else y0
                y1r = min(R, gm_h) if y1 <= h else h - y1
                x0r = min(R, gm_w) if x0 >= 0 else x0
                x1r = min(R, gm_w) if x1 <= w else w - x1
    
                heat_map[y - y0r : y + y1r, x - x0r : x + x1r] = \
                    np.maximum(heat_map[y - y0r : y + y1r, x - x0r : x + x1r], tmp_gaussian_map[gm_h-y0r:gm_h+y1r, gm_w-x0r:gm_w+x1r])
    
    densebox = densebox - np.tile(densebox_anchor, (4, 1, 1))
    densebox = densebox * mask[np.newaxis, :, :]
    return heat_map, border_map, geometry_map, training_mask, densebox, phoc_map

class IC15Loader(data.Dataset):
    def __init__(self, root_dir, is_transform=False, img_size=None, kernel_num=7, min_scale=0.4, kwd=None):
        self.is_transform = is_transform
        
        self.img_size = img_size if (img_size is None or isinstance(img_size, tuple)) else (img_size, img_size)
        self.kernel_num = kernel_num
        self.min_scale = min_scale

        data_dirs = [root_dir]
        gt_dirs = [root_dir]

        self.img_paths = []
        self.gt_paths = []
        self.random_scale = np.array([0.8, 1, 1.5, 2.0, 2.5, 3.0])
        self.background_ratio = 3. / 8.
        self.input_size = 640
        self.aug = augument()
        for data_dir, gt_dir in zip(data_dirs, gt_dirs):
            img_names = util.io.ls(data_dir, '.jpg')
            img_names.extend(util.io.ls(data_dir, '.png'))
            # img_names.extend(util.io.ls(data_dir, '.gif'))

            img_paths = []
            gt_paths = []
            for idx, img_name in enumerate(tqdm(img_names)):
                img_path = data_dir + img_name
                gt_name = ".".join(img_name.split('.')[:-1]) + '.txt'
                gt_path = gt_dir + gt_name
                    
                if not os.path.exists(gt_path):
                    continue
                img_paths.append(img_path)
                gt_paths.append(gt_path)

            self.img_paths.extend(img_paths)
            self.gt_paths.extend(gt_paths)
        indexes = list(range(len(self.img_paths)))
        random.shuffle(indexes)
        self.img_paths = list(map(lambda x: self.img_paths[x], indexes))
        self.gt_paths = list(map(lambda x: self.gt_paths[x], indexes))

        self.keywords = None
        if kwd is not None:
            with open(kwd, 'r') as f:
                self.keywords = json.load(f)
        print(self.keywords)

    def __len__(self): 
        return len(self.img_paths)

    def __getitem__(self, index):
        """
        Generate maps for probability_map, threshold_map
        """
        
        img_path = self.img_paths[index]
        gt_path = self.gt_paths[index]

        img = get_img(img_path)
        h, w = img.shape[:2]
        bboxes, tags, texts = get_bboxes(img, gt_path, keywords=self.keywords)
        print(texts)
        texts = texts if self.keywords is not None else None
        bboxes, tags, texts = check_and_validate_polys(bboxes, tags, (h, w), texts)

        rd_scale = np.random.choice(self.random_scale)
        img = cv2.resize(img, dsize=None, fx=rd_scale, fy=rd_scale)
        bboxes *= rd_scale
        
        # else:
        img, bboxes, tags, texts = crop_area_v2(img, bboxes, tags, texts=texts, crop_background=False)
        img = img.copy()
        # resize the image to input size
        new_h, new_w, _ = img.shape
        max_h_w_i = np.max([new_h, new_w, self.input_size])
        im_padded = np.zeros((max_h_w_i, max_h_w_i, 3), dtype=np.uint8)
        im_padded[:new_h, :new_w, :] = img.copy()
    
        img = cv2.resize(im_padded, dsize=(self.input_size, self.input_size))

        new_h, new_w, _ = im_padded.shape
        resize_h = self.input_size
        resize_w = self.input_size
        img = cv2.resize(img, dsize=(resize_w, resize_h))
        resize_ratio_3_x = resize_w/float(new_w)
        resize_ratio_3_y = resize_h/float(new_h)
        bboxes[:, :, 0] *= resize_ratio_3_x
        bboxes[:, :, 1] *= resize_ratio_3_y

        h, w = img.shape[0:2]
        gt_text = np.zeros(img.shape[0:2], dtype='uint8')
        border_map = np.zeros((h, w), dtype='float32')
        geo_map = np.zeros((4, h, w), dtype='float32')
        training_mask = np.ones(img.shape[0:2], dtype='uint8')
        densebox = np.zeros((8, h, w), np.float32)

        phoc_map = np.zeros((len(self.keywords), h, w))

        if bboxes.shape[0] > 0:
            bboxes = bboxes.astype(np.int32)
            h, w = img.shape[:2]
            t1 = time.time()
            gt_text, border_map, geo_map, training_mask, densebox, phoc_map = generate_gaussian_target(bboxes, h, w, training_mask, texts, self.keywords)
            dur = time.time() - t1
            for i in range(bboxes.shape[0]):
                if not tags[i]:
                    cv2.drawContours(training_mask, [bboxes[i]], -1, 0, -1)

        # if np.random.uniform(0, 1) > 0.7:
        #     img, border_map, gt_text, training_mask, phoc_map = random_rotate([img, border_map, gt_text, training_mask, phoc_map])
            
        if self.is_transform:
            img = Image.fromarray(img)
            img = img.convert('RGB')
#             img = transforms.ColorJitter(brightness = 32.0 / 255, saturation = 0.5)(img)
        else:
            img = Image.fromarray(img)
            img = img.convert('RGB')
        ori_img = img
        
        img = self.aug(image=np.array(img))['image']
        img = transforms.ToTensor()(img)
        img = transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])(img)
        
        probability_map = torch.from_numpy(gt_text).float()
        training_mask = torch.from_numpy(training_mask).float()
        border_map = torch.from_numpy(border_map).float()
        geo_map = torch.from_numpy(geo_map).float()
        densebox = torch.from_numpy(densebox).float()
        phoc_map = torch.from_numpy(phoc_map).float()

        return img, probability_map, training_mask, np.array(ori_img).transpose((2, 0, 1)), border_map, geo_map, densebox, phoc_map

if __name__ == "__main__":
    import matplotlib.pyplot as plt
    root_dir = sys.argv[1]
    ic15dataset = IC15Loader(root_dir=root_dir, is_transform=True, img_size=512, kwd='assert/word_dict.json')

    for item in ic15dataset:
        img, pb_map, train_mask, ori_img, border_map, geo_map, densebox, phoc_map = item
        ori_img = ori_img.transpose((1, 2, 0))
        # print(f'img_shape:{img.shape}, pb_map_shape:{pb_map.shape}, densebox:{densebox.shape}')
        seg_map_3c = np.repeat(pb_map[:, :, None].numpy(),3,2)*255
        densebox = densebox.numpy()[2]
        dense_heatmap=cv2.applyColorMap(densebox.astype(np.uint8), cv2.COLORMAP_JET)

        heatmap=cv2.applyColorMap(seg_map_3c.astype(np.uint8), cv2.COLORMAP_JET)
        
        # save_img=np.concatenate((np.array(ori_img),att_im),1)
        region = np.where(seg_map_3c[:, :, 0] > int(0 * 255), np.ones_like(seg_map_3c[:, :, 0]) * 255, np.zeros_like(seg_map_3c[:, :, 0]))
        other_region = np.where(seg_map_3c[:, :, 0] > int(0. * 255), seg_map_3c[:, :, 0], np.zeros_like(seg_map_3c[:, :, 0]))
        region = phoc_map[76, :, :].numpy() * 255
        heatmap=cv2.applyColorMap(region.astype(np.uint8), cv2.COLORMAP_JET)
        att_im = cv2.addWeighted(heatmap, 0.7, np.array(ori_img)[:, :,::-1], 0.2, 0.0)
        # cv2.imshow('heatmap', dense_heatmap)
        cv2.imshow('pb_map', att_im)

        cv2.waitKey()
        cv2.destroyAllWindows()
        