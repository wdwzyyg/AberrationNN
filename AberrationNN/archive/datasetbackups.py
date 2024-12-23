import json

import numpy as np
import torch
import os
import torch.nn.functional as F
from AberrationNN.utils import polar2cartesian, evaluate_aberration_derivative_cartesian, evaluate_aberration_cartesian
import itertools
import pandas as pd
from skimage import filters
from random import randrange

wavelength_A = 0.025079340317328468

def map01(mat):
    return (mat - mat.min()) / (mat.max() - mat.min())


def hp_filter(img):
    return filters.butterworth(np.array(img).astype('float32'), cutoff_frequency_ratio=0.05,
                               order=3, high_pass=True, squared_butterworth=True, npad=0)

def ronchis2ffts(image_d, image_o, patch, fft_pad_factor, if_hann, if_pre_norm):
    """
    take the processed ronchigrams as input, generate FFT difference patch for the direct input for model
    :param if_pre_norm:
    :param if_hann:
    :param fft_pad_factor:
    :param patch: patch size
    :param image_o: overfocus ronchigram
    :param image_d: defocus ronchigram
    :return: FFT difference patches
    """

    isize = patch * fft_pad_factor
    csize = isize
    n = int(image_o.shape[0] / patch)

    topc = isize // 2 - csize // 2
    leftc = isize // 2 - csize // 2
    bottomc = isize // 2 + csize // 2
    rightc = isize // 2 + csize // 2

    hanning = np.outer(np.hanning(patch), np.hanning(patch))  # A 2D hanning window with the same size as image

    top = isize // 2 - patch // 2
    left = isize // 2 - patch // 2
    bottom = isize // 2 + patch // 2
    right = isize // 2 + patch // 2

    # image_d = map01(np.log(image_d))
    if if_pre_norm:
        image_d = map01(image_d)
        image_o = map01(image_o)

    windows = image_d.unfold(0, patch, patch)
    windows = windows.unfold(1, patch, patch)
    windows_fft = torch.zeros((n, n, csize, csize))  #############
    for (i, j) in itertools.product(range(n), range(n)):
        tmp = torch.zeros((isize, isize))
        img = windows[i][j]
        if if_hann:
            img *= hanning
        tmp[top:bottom, left:right] = img
        tmpft = torch.fft.fft2(tmp)
        tmpft = torch.fft.fftshift(tmpft)
        windows_fft[i][j] = np.abs(tmpft[topc:bottomc, leftc:rightc])
    #####################################################################################
    # image_o = map01(np.log(image_o))  # log does not make a difference in exp
    if if_pre_norm:
        image_o = map01(image_o)

    windows2 = image_o.unfold(0, patch, patch)
    windows2 = windows2.unfold(1, patch, patch)
    windows_fft2 = torch.zeros((n, n, csize, csize))
    for (i, j) in itertools.product(range(n), range(n)):
        tmp = torch.zeros((isize, isize))
        img = windows2[i][j]
        if if_hann:
            img *= hanning
        tmp[top:bottom, left:right] = img
        tmpft = torch.fft.fft2(tmp)
        tmpft = torch.fft.fftshift(tmpft)
        windows_fft2[i][j] = np.abs(tmpft[topc:bottomc, leftc:rightc])

    image = windows_fft.reshape(n ** 2, csize, csize) - windows_fft2.reshape(n ** 2, csize, csize)
    return image


class RonchiTiltPairAll:

    def __init__(self, data_dir, filestart=0, filenum=120, nimage=100, pre_normalization=False, normalization=True,
                 transform=None,patch=32, imagesize=512, downsampling=2, if_HP=True, if_reference=False):
        filenum = len(os.listdir(data_dir))
        nimage = np.load(data_dir + os.listdir(data_dir)[0] + '/ronchi_stack.npz')['tiltx'].shape[0]

        self.data_dir = data_dir
        # folder name + index number 000-099
        self.ids = [i + "%03d" % j for i in [*os.listdir(data_dir)[filestart:filestart + filenum]] for j in
                    [*range(nimage)]]
        self.normalization = normalization
        self.pre_normalization = pre_normalization
        self.transform = transform
        self.patch = patch
        self.imagesize = imagesize
        self.downsampling = downsampling
        self.if_HP = if_HP
        self.if_reference = if_reference

    def __getitem__(self, i):
        img_id = self.ids[i]  # folder names and index number 000-099
        image = self.get_image(img_id)
        target = self.get_target(img_id)
        return image, target

    def __len__(self):
        return len(self.ids)

    def get_image(self, img_id):
        path = self.data_dir + img_id[:-3] + '/ronchi_stack.npz'
        image_x = np.load(path)['tiltx'][int(img_id[-3:])]
        image_nx = np.load(path)['tiltnx'][int(img_id[-3:])]
        if self.if_HP:
            image_x = hp_filter(image_x)
            image_nx = hp_filter(image_nx)
        image_x = torch.as_tensor(image_x, dtype=torch.float32)
        image_nx = torch.as_tensor(image_nx, dtype=torch.float32)
        if self.downsampling is not None and self.downsampling > 1:
            image_x = F.interpolate(image_x[None, None, ...], scale_factor=1 / self.downsampling, mode='bilinear')[0, 0]
            image_nx = F.interpolate(image_nx[None, None, ...], scale_factor=1 / self.downsampling, mode='bilinear')[
                0, 0]
        if self.transform:
            image_x = self.transform(image_x)
            image_nx = self.transform(image_nx)

        image1 = ronchis2ffts(image_x, image_nx, self.patch, 2, True, self.pre_normalization)

        image_y = np.load(path)['tilty'][int(img_id[-3:])]
        image_ny = np.load(path)['tiltny'][int(img_id[-3:])]
        if self.if_HP:
            image_y = hp_filter(image_y)
            image_ny = hp_filter(image_ny)

        image_y = torch.as_tensor(image_y, dtype=torch.float32)
        image_ny = torch.as_tensor(image_ny, dtype=torch.float32)
        if self.downsampling is not None and self.downsampling > 1:
            image_y = F.interpolate(image_y[None, None, ...], scale_factor=1 / self.downsampling, mode='bilinear')[0, 0]
            image_ny = F.interpolate(image_ny[None, None, ...], scale_factor=1 / self.downsampling, mode='bilinear')[
                0, 0]
        if self.transform:
            image_y = self.transform(image_y)
            image_ny = self.transform(image_ny)

        image2 = ronchis2ffts(image_y, image_ny, self.patch, 2, True, self.pre_normalization)

        image = torch.cat([image1, image2], dim=0)

        if self.if_reference:
            # not up-to-date
            reference = np.load(self.data_dir + img_id[:-3] + '/standard_reference.npz')  ##########
            image_x = torch.as_tensor(reference['tiltx'], dtype=torch.float32)
            image_nx = torch.as_tensor(reference['tiltnx'], dtype=torch.float32)
            if self.downsampling is not None and self.downsampling > 1:
                image_x = F.interpolate(image_x[None, None, ...], scale_factor=1 / self.downsampling, mode='bilinear')[
                    0, 0]
                image_nx = \
                    F.interpolate(image_nx[None, None, ...], scale_factor=1 / self.downsampling, mode='bilinear')[0, 0]
            image_rf1 = ronchis2ffts(image_x, image_nx, self.patch, 2, True, self.pre_normalization)

            image_y = torch.as_tensor(reference['tilty'], dtype=torch.float32)
            image_ny = torch.as_tensor(reference['tiltny'], dtype=torch.float32)
            if self.downsampling is not None and self.downsampling > 1:
                image_y = F.interpolate(image_y[None, None, ...], scale_factor=1 / self.downsampling, mode='bilinear')[
                    0, 0]
                image_ny = \
                    F.interpolate(image_ny[None, None, ...], scale_factor=1 / self.downsampling, mode='bilinear')[0, 0]
            image_rf2 = ronchis2ffts(image_y, image_ny, self.patch, 2, True, self.pre_normalization)

            # then not decided how to use the reference yet.

        if self.normalization:
            image = torch.where(image >= 0, image / image.max(), -image / image.min())

            return image

    def get_target(self, img_id):
        # return shape need to be [x]
        target = pd.read_csv(self.data_dir + img_id[:-3] + '/meta.csv')  ###########
        target = target.get(['C10', 'C12', 'phi12', 'C21', 'phi21', 'C23', 'phi23', 'Cs']).to_numpy()[
            int(img_id[-3:])]  ##########
        target = torch.as_tensor(target, dtype=torch.float32)  ##### important to keep same dtype
        polar = {'C10': target[0], 'C12': target[1], 'phi12': target[2],
                 'C21': target[3], 'phi21': target[4], 'C23': target[5], 'phi23': target[6]}
        car = polar2cartesian(polar)
        allab = [car['C10'], car['C12a'], car['C12b'],
                 car['C21a'], car['C21b'], car['C23a'], car['C23b']]
        allab = torch.as_tensor(allab, dtype=torch.float32)

        return allab

class MagnificationDataset:
    """
    Default operations:
    image: map01, downsample by 2, FFT, difference, FFT off-focus A patches - FFT off-focus B patches
    (first key - second key)
    target: polar transformed into cartesian, all in angstroms.
    Example:
    :argument:


    """

    def __init__(self, data_dir, filestart=0, pre_normalization=False, normalization=True,
                 transform=None, patch=64, imagesize=512, cropsize = 192, overlap=0, downsampling=1, fft_pad_factor = 4,
                 fftcropsize = 128, if_HP=True, target_high_order=False, picked_keys=None):
        if picked_keys is None:
            picked_keys = [0, 1]
        self.picked_keys = picked_keys
        self.keys = np.array(list(np.load(data_dir + os.listdir(data_dir)[0] + '/ronchi_stack.npz').keys()))[self.picked_keys]
        self.data_dir = data_dir
        filenum = len(os.listdir(data_dir))
        nimage = np.load(data_dir + os.listdir(data_dir)[0] + '/ronchi_stack.npz')[self.keys[0]].shape[0]
        # folder name + index number 000-099
        self.ids = [i + "%03d" % j for i in [*os.listdir(data_dir)[filestart:filestart + filenum]] for j in
                    [*range(nimage)]]
        self.normalization = normalization
        self.pre_normalization = pre_normalization

        self.transform = transform
        self.patch = patch
        self.imagesize = imagesize
        self.cropsize = cropsize
        self.downsampling = downsampling
        self.if_HP = if_HP
        self.fft_pad_factor = fft_pad_factor
        self.fftcropsize = fftcropsize
        self.target_high_order = target_high_order
        self.overlap = overlap

    def __getitem__(self, i):
        img_id = self.ids[i]  # folder names and index number 000-099
        image, xi, yi = self.get_image(img_id)
        [du2, dv2, duv] = self.get_target(img_id)
        pick_du2 = du2[
                   xi * self.patch * self.downsampling: (xi + 1) * self.patch * self.downsampling,
                   yi * self.patch * self.downsampling: (yi + 1) * self.patch * self.downsampling].mean()
        pick_dv2 = dv2[
                   xi * self.patch * self.downsampling: (xi + 1) * self.patch * self.downsampling,
                   yi * self.patch * self.downsampling: (yi + 1) * self.patch * self.downsampling].mean()
        pick_duv = duv[
                   xi * self.patch * self.downsampling: (xi + 1) * self.patch * self.downsampling,
                   yi * self.patch * self.downsampling: (yi + 1) * self.patch * self.downsampling].mean()
        target = [pick_du2, pick_dv2, pick_duv]
        target = torch.as_tensor(target, dtype=torch.float32)
        meta = self.get_meta(img_id)
        return image, target, meta

    def __len__(self):
        return len(self.ids)

    def singleFFT(self, im_list):
        ffts = []
        isize = self.patch * self.fft_pad_factor
        csize = isize
        topc = isize // 2 - csize // 2
        leftc = isize // 2 - csize // 2
        bottomc = isize // 2 + csize // 2
        rightc = isize // 2 + csize // 2

        hanning = np.outer(np.hanning(self.patch),
                           np.hanning(self.patch))  # A 2D hanning window with the same size as image

        top = isize // 2 - self.patch // 2
        left = isize // 2 - self.patch // 2
        bottom = isize // 2 + self.patch // 2
        right = isize // 2 + self.patch // 2
        for im in im_list:
            picked = im
            if self.if_HP:
                picked = hp_filter(im)
                picked = torch.as_tensor(picked, dtype=torch.float32)
            if self.downsampling is not None and self.downsampling > 1:
                picked = F.interpolate(picked[None, None, ...], scale_factor=1 / self.downsampling, mode='bilinear')[0, 0]
            if self.transform:
                picked = torch.as_tensor(picked, dtype=torch.float32)
                picked = self.transform(picked)

            if self.pre_normalization:
                picked = map01(picked)
            tmp = torch.zeros((isize, isize))
            tmp[top:bottom, left:right] = picked * hanning
            tmpft = torch.fft.fft2(tmp)
            tmpft = torch.fft.fftshift(tmpft)
            fft = np.abs(tmpft[topc:bottomc, leftc:rightc])

            if self.normalization:
                fft = (fft - fft.min()) / (fft.max()-fft.min())
            ffts.append(fft)
        return torch.cat([it[None, ...] for it in ffts])

    def check_chi(self, img_id):
        # just calculate the whole function array here, no downsampling considered
        target = pd.read_csv(self.data_dir + img_id[:-3] + '/meta.csv')  ###########

        path = self.data_dir + img_id[:-3] + '/ronchi_stack.npz'
        image_in = np.load(path)[self.keys[0]][int(img_id[-3:])][self.cropsize:-self.cropsize,
                   self.cropsize:-self.cropsize]  # crop the outer border
        gpts = image_in.shape[0]
        sampling = target.get(['k_sampling_mrad']).to_numpy()[int(img_id[-3:])]
        k = (np.arange(gpts) - gpts / 2) * sampling * 1e-3
        kxx, kyy = np.meshgrid(*(k, k), indexing="ij")  # A-1

        target = target.get(['C10', 'C12', 'phi12', 'C21', 'phi21', 'C23', 'phi23', 'Cs']).to_numpy()[
            int(img_id[-3:])]  ##########
        # target = torch.as_tensor(target, dtype=torch.float32)  ##### important to keep same dtype
        polar = {'C10': target[0], 'C12': target[1], 'phi12': target[2],
                 'C21': target[3], 'phi21': target[4], 'C23': target[5], 'phi23': target[6], 'C30': target[7]}
        car = polar2cartesian(polar)
        phase_shift = evaluate_aberration_cartesian(car, kxx, kyy, wavelength_A*1e-10)
        if phase_shift.max() > (2 * np.pi):
            print('Exceeded 2 pi')
            return phase_shift
        else:
            return phase_shift

    def get_image(self, img_id):
        path = self.data_dir + img_id[:-3] + '/ronchi_stack.npz'
        image = np.load(path)[self.keys[0]][int(img_id[-3:])][self.cropsize:-self.cropsize, self.cropsize:-self.cropsize]  # crop the outer border
        rrange = int(image.shape[0] / self.patch / self.downsampling)
        xi = randrange(0, rrange)
        yi = randrange(0, rrange)
        data, data_rf = [], []
        for k in self.keys:
            image = np.load(path)[k][int(img_id[-3:])][self.cropsize:-self.cropsize, self.cropsize:-self.cropsize]
            if self.transform:
                image= torch.as_tensor(image, dtype=torch.float32)
                image = self.transform(image)
            # crop the outer border
            # pick a patch
            data.append(image[
                        xi * self.patch * self.downsampling: (xi + 1) * self.patch * self.downsampling,
                        yi * self.patch * self.downsampling: (yi + 1) * self.patch * self.downsampling])
        image_aberration = self.singleFFT(data)
        if image_aberration.shape[-1] > self.fftcropsize:
            image_aberration = image_aberration[:, self.fftcropsize//2: -self.fftcropsize//2, self.fftcropsize//2: -self.fftcropsize//2]

        try:
            path_rf = self.data_dir + img_id[:-3] + '/standard_reference_d_o.npy'
            for i in range(len(self.keys)):
                croped = np.load(path_rf)[i][self.cropsize:-self.cropsize, self.cropsize:-self.cropsize]
                if self.transform:
                    croped = torch.as_tensor(croped, dtype=torch.float32)
                    croped = self.transform(croped)
                data_rf.append(croped[
                        xi * self.patch * self.downsampling: (xi + 1) * self.patch * self.downsampling,
                        yi * self.patch * self.downsampling: (yi + 1) * self.patch * self.downsampling]) # crop the outer border
        except:
            path_rf = self.data_dir + img_id[:-3] + '/standard_reference.npz'
            for k in self.keys:
                rf = np.load(path_rf)[k]
                rf = rf if rf.ndim==2 else rf[0]
                croped = rf[self.cropsize:-self.cropsize, self.cropsize:-self.cropsize]
                data_rf.append(croped[
                            xi * self.patch * self.downsampling: (xi + 1) * self.patch * self.downsampling,
                            yi * self.patch * self.downsampling: (yi + 1) * self.patch * self.downsampling])
        image_reference = self.singleFFT(data_rf)
        if image_reference.shape[-1] > self.fftcropsize:
            image_reference = image_reference[:, self.fftcropsize//2: -self.fftcropsize//2, self.fftcropsize//2: -self.fftcropsize//2]

        return torch.cat([image_aberration, image_reference]), xi, yi

    def get_meta(self, img_id):
        meta = pd.read_csv(self.data_dir + img_id[:-3] + '/meta.csv')  ###########
        meta = meta.get(['thicknessA', 'tiltx', 'tilty', 'C10', 'C12', 'phi12', 'C21', 'phi21', 'C23', 'phi23', 'Cs']).to_numpy()[int(img_id[-3:])]
        return meta

    def get_target(self, img_id):
        # return shape need to be [x]
        # just calculate the whole function array here, no downsampling considered
        target = pd.read_csv(self.data_dir + img_id[:-3] + '/meta.csv')  ###########
        path = self.data_dir + img_id[:-3] + '/ronchi_stack.npz'
        image_in = np.load(path)[self.keys[0]][int(img_id[-3:])][self.cropsize:-self.cropsize,
                   self.cropsize:-self.cropsize]  # crop the outer border
        gpts = image_in.shape[0]
        sampling = target.get(['k_sampling_mrad']).to_numpy()[int(img_id[-3:])]
        k = (np.arange(gpts) - gpts / 2) * sampling * 1e-3
        kxx, kyy = np.meshgrid(*(k, k), indexing="ij")  # rad

        target = target.get(['C10', 'C12', 'phi12', 'C21', 'phi21', 'C23', 'phi23', 'Cs']).to_numpy()[
            int(img_id[-3:])]  ##########
        # target = torch.as_tensor(target, dtype=torch.float32)  ##### important to keep same dtype
        polar_l = {'C10': target[0], 'C12': target[1], 'phi12': target[2],
                   'C21': target[3], 'phi21': target[4], 'C23': target[5], 'phi23': target[6], 'C30': target[7]}

        polar_h = dict(pd.read_json(self.data_dir + img_id[:-3] + '/global_p.json', orient='index')[0])
        del polar_h['real_sampling_A']
        del polar_h['voltage_ev']
        del polar_h['focus_spread_A']

        car = polar2cartesian({**polar_l, **polar_h})
        return evaluate_aberration_derivative_cartesian(car, kxx, kyy, wavelength_A*1e-10, if_highorder=self.target_high_order)

    def data_shape(self):
        return self.get_image(self.ids[0])[0].shape

    def get_wholeimage_eval(self, img_id):
        #### downsampling not considered here yet
        alldata = []

        for k in self.keys:
            path = self.data_dir + img_id[:-3] + '/ronchi_stack.npz'
            image = np.load(path)[k][int(img_id[-3:])][self.cropsize:-self.cropsize, self.cropsize:-self.cropsize]  # crop the outer border
            image = torch.from_numpy(image).float()
            windows = image.unfold(0, self.patch, self.patch - self.overlap)
            windows = windows.unfold(1, self.patch, self.patch - self.overlap)

            image_aberration = self.singleFFT(list(torch.flatten(windows, start_dim=0, end_dim=1)))
            if image_aberration.shape[-1] > self.fftcropsize:
                image_aberration = image_aberration[:, self.fftcropsize//2: -self.fftcropsize//2, self.fftcropsize//2: -self.fftcropsize//2]
            alldata.append(image_aberration)
        for k in self.keys:
            path_rf = self.data_dir + img_id[:-3] + '/standard_reference.npz'
            rf = np.load(path_rf)[k]
            rf = rf if rf.ndim == 2 else rf[0]
            cropped = rf[self.cropsize:-self.cropsize, self.cropsize:-self.cropsize]
            cropped = torch.from_numpy(cropped).float()
            windows_ = cropped.unfold(0, self.patch, self.patch - self.overlap)
            windows_ = windows_.unfold(1, self.patch, self.patch - self.overlap)
            image_reference = self.singleFFT(list(torch.flatten(windows_, start_dim=0, end_dim=1)))
            if image_reference.shape[-1] > self.fftcropsize:
                image_reference = image_reference[:, self.fftcropsize//2: -self.fftcropsize//2, self.fftcropsize//2: -self.fftcropsize//2]
            alldata.append(image_reference)

        return torch.swapaxes(torch.stack(alldata),0,1)