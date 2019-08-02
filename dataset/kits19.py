import json
from pathlib import Path

import cv2
import numpy as np
import torch
from albumentations import (
    PadIfNeeded,
    Compose,
    Resize
)
from torch.utils import data

from dataset.transform import to_numpy


class KiTS19(data.Dataset):
    def __init__(self, root, stack_num=1, spec_classes=None, img_size=(512, 512),
                 train_case_ids_file='train.txt', valid_case_ids_file='val.txt', test_case_ids_file='test.txt',
                 roi_file=None, roi_error_range=0,
                 train_transform=None, valid_transform=None, test_transform=None):
        self._root = Path(root)
        self._stack_num = stack_num
        if spec_classes is None:
            self._spec_classes = [0, 1, 2]
        else:
            assert len(self.get_classes_name(spec=False)) == len(spec_classes)
            self._spec_classes = spec_classes
        
        self._img_size = img_size
        
        self._rois = None
        if roi_file is not None:
            _roi_file = self._root / roi_file
            with open(_roi_file, 'r') as f:
                self._rois = json.load(f)
        self._roi_error_range = roi_error_range
        
        self._train_transform = train_transform
        self._valid_transform = valid_transform
        self._test_transform = test_transform
        
        self._get_data(train_case_ids_file, valid_case_ids_file, test_case_ids_file)
        self._split_subset()
        
        self._num_classes = len(self.get_classes_name())
        self._img_channels = self.__getitem__(0)['image'].shape[0]
    
    def _get_data(self, train_case_ids_file, valid_case_ids_file, test_case_ids_file):
        def read_txt(file):
            d = []
            f = open(file, 'r')
            for line in f:
                d.append(int(line))
            return d
        
        train_case_ids_file = self._root / train_case_ids_file
        valid_case_ids_file = self._root / valid_case_ids_file
        test_case_ids_file = self._root / test_case_ids_file
        train_case = read_txt(train_case_ids_file)
        valid_case = read_txt(valid_case_ids_file)
        test_case = read_txt(test_case_ids_file)
        
        train_imgs, train_labels, train_case_slice_num = self._read_npy(self._root, train_case, is_test=False)
        valid_imgs, valid_labels, valid_case_slice_num = self._read_npy(self._root, valid_case, is_test=False)
        test_imgs, test_labels, test_case_slice_num = self._read_npy(self._root, test_case, is_test=True)
        
        self._imgs = train_imgs + valid_imgs + test_imgs
        self._labels = train_labels + valid_labels + test_labels
        
        self._indices = list(range(len(self._imgs)))
        self._train_indices = self._indices[:len(train_imgs)]
        self._valid_indices = self._indices[len(train_imgs):len(train_imgs) + len(valid_imgs)]
        self._test_indices = self._indices[
                             len(train_imgs) + len(valid_imgs): len(train_imgs) + len(valid_imgs) + len(test_imgs)]
        
        self._train_case_slice_num = train_case_slice_num
        self._valid_case_slice_num = valid_case_slice_num
        self._test_case_slice_num = test_case_slice_num
        
        self._case_slice_indices = [0]
        idx = 0
        for num in self._train_case_slice_num + self._valid_case_slice_num + self._test_case_slice_num:
            idx += num
            self._case_slice_indices.append(idx)
    
    def _read_npy(self, root, cases, is_test=False):
        imgs = []
        labels = []
        case_slice_num = []
        
        for case in cases:
            case_root = root / f'case_{case:05d}'
            imaging_dir = case_root / 'imaging'
            assert imaging_dir.exists()
            case_imgs = sorted(list(imaging_dir.glob('*.npy')))
            
            min_z = 0
            max_z = len(case_imgs)
            if self._rois is not None:
                roi = self._rois[f'case_{case:05d}']['kidney']
                min_z = max(min_z, roi['min_z'] - self._roi_error_range)
                max_z = min(max_z, roi['max_z'] + self._roi_error_range)
            
            case_imgs = case_imgs[min_z: max_z]
            imgs += case_imgs
            
            if not is_test:
                segmentation_dir = case_root / 'segmentation'
                assert segmentation_dir.exists()
                case_labels = sorted(list(segmentation_dir.glob('*.npy')))
                case_labels = case_labels[min_z: max_z]
                labels += case_labels
                assert len(imgs) == len(labels)
            
            case_slice_num.append(len(case_imgs))
        
        return imgs, labels, case_slice_num
    
    def _split_subset(self):
        self._train_dataset = data.Subset(self, self._train_indices)
        self._valid_dataset = data.Subset(self, self._valid_indices)
        self._test_dataset = data.Subset(self, self._test_indices)
    
    def get_classes_name(self, spec=True):
        classes_name = np.array(['background', 'kidney', 'tumor'])
        
        if not spec:
            return classes_name
        
        spec_classes_name = []
        for i in classes_name[self._spec_classes]:
            if i not in spec_classes_name:
                spec_classes_name.append(i)
        return spec_classes_name
    
    def get_colormap(self, spec=True):
        cmap = [[0, 0, 0], [255, 0, 0], [0, 0, 255]]
        cmap = np.array(cmap, dtype=np.int)
        
        if not spec:
            return cmap
        
        spec_cmap = []
        for i in cmap[self._spec_classes]:
            if len(spec_cmap) == 0:
                spec_cmap.append(i)
            else:
                duplicate = False
                for j in spec_cmap:
                    duplicate = duplicate or (i == j).all()
                if not duplicate:
                    spec_cmap.append(i)
        return np.array(spec_cmap)
    
    def idx_to_name(self, idx):
        path = self._imgs[idx]
        name = Path(path.parts[-3]) / Path(path.parts[-1][:-4])
        return name
    
    def vis_transform(self, imgs=None, labels=None, preds=None, to_plt=False):
        cmap = self.get_colormap()
        if imgs is not None:
            if type(imgs).__module__ != np.__name__:
                imgs = imgs.cpu().detach().numpy()
            if to_plt is True:
                imgs = imgs.transpose((0, 2, 3, 1))
        
        if labels is not None:
            if type(labels).__module__ != np.__name__:
                labels = labels.cpu().detach().numpy().astype('int')
            labels = cmap[labels]
            labels = labels.transpose((0, 3, 1, 2))
            if to_plt is True:
                labels = labels.transpose((0, 2, 3, 1))
            labels = labels / 255.
        
        if preds is not None:
            if type(preds).__module__ != np.__name__:
                preds = preds.cpu().detach().numpy()
            if preds.shape[1] == self.num_classes:
                preds = preds.argmax(axis=1)
            preds = cmap[preds]
            preds = preds.transpose((0, 3, 1, 2))
            if to_plt is True:
                preds = preds.transpose((0, 2, 3, 1))
            preds = preds / 255.
        
        return imgs, labels, preds
    
    def _default_transform(self, data):
        if (data['image'].shape[0], data['image'].shape[1]) != self._img_size:
            data = self._resize(data)
        
        image, label = data['image'], data['label']
        
        image = image.astype(np.float32)
        image = image.transpose((2, 0, 1))
        image = torch.from_numpy(image)
        data['image'] = image
        
        if label is not None:
            label = label.astype(np.int64)
            
            if self._spec_classes != [0, 1, 2]:
                idx = list(range(len(self.get_classes_name(spec=False))))
                masks = [np.where(label == i) for i in idx]
                spec_class_idx = []
                for i in self._spec_classes:
                    if i not in spec_class_idx:
                        spec_class_idx.append(i)
                
                for mask, spec_class in zip(masks, self._spec_classes):
                    label[mask] = spec_class_idx.index(spec_class)
            
            label = torch.from_numpy(label)
            data['label'] = label
        
        return data
    
    def _resize(self, data):
        data = to_numpy(data)
        img, label = data['image'], data['label']
        
        num = max(img.shape[0], img.shape[1])
        
        aug = Compose([
            PadIfNeeded(min_height=num, min_width=num,
                        border_mode=cv2.BORDER_CONSTANT, p=1),
            Resize(height=self._img_size[0], width=self._img_size[1], p=1)
        ])
        
        data = aug(image=img, mask=label)
        img, label = data['image'], data['mask']
        
        data['image'] = img
        data['label'] = label
        return data
    
    def img_idx_to_case_idx(self, idx):
        case_idx = 0
        for i in range(len(self._case_slice_indices) - 1):
            if self._case_slice_indices[i] <= idx < self._case_slice_indices[i + 1]:
                case_idx = i
                break
        return case_idx
    
    def get_stack_img(self, idx):
        case_idx = self.img_idx_to_case_idx(idx)
        imgs = []
        for i in range(idx - self._stack_num // 2, idx + self._stack_num // 2 + 1):
            if i < self._case_slice_indices[case_idx]:
                i = self._case_slice_indices[case_idx]
            elif i >= self._case_slice_indices[case_idx + 1]:
                i = self._case_slice_indices[case_idx + 1] - 1
            img_path = self._imgs[i]
            img = np.load(str(img_path))
            imgs.append(img)
        img = np.stack(imgs, axis=2)
        
        if idx in self._test_indices:
            label = None
        else:
            label_path = self._labels[idx]
            label = np.load(str(label_path))
        
        roi = None if self._rois is None else self._rois[f'case_{case_idx:05d}']['kidney']
        data = {'image': img, 'label': label, 'index': idx, 'roi': roi}
        
        return data
    
    def __getitem__(self, idx):
        data = self.get_stack_img(idx)
        
        if idx in self._train_indices and self._train_transform is not None:
            data = self._train_transform(data)
        elif idx in self._valid_indices and self._valid_transform is not None:
            data = self._valid_transform(data)
        elif idx in self._test_indices and self._test_transform is not None:
            data = self._test_transform(data)
        
        data = self._default_transform(data)
        
        return data
    
    def __len__(self):
        return len(self._imgs)
    
    @property
    def img_channels(self):
        return self._img_channels
    
    @property
    def num_classes(self):
        return self._num_classes
    
    @property
    def spec_classes(self):
        return self._spec_classes
    
    @property
    def train_dataset(self):
        return self._train_dataset
    
    @property
    def valid_dataset(self):
        return self._valid_dataset
    
    @property
    def test_dataset(self):
        return self._test_dataset


import click


@click.command()
@click.option('--data', 'data_path', help='kits19 data path',
              type=click.Path(exists=True, dir_okay=True, resolve_path=True),
              default='data', show_default=True)
def main(data_path):
    root = Path(data_path)
    
    dataset = KiTS19(root, stack_num=3, spec_classes=[0, 1, 2], img_size=(512, 512),
                     roi_file='roi.json', roi_error_range=5,
                     train_transform=None, valid_transform=None, test_transform=None)
    from torch.utils.data import DataLoader, SequentialSampler
    from utils.vis import imshow
    
    subset = dataset.valid_dataset
    sampler = SequentialSampler(subset)
    data_loader = DataLoader(subset, batch_size=1, sampler=sampler)
    
    for batch_idx, data in enumerate(data_loader):
        imgs, labels, idx = data['image'], data['label'], data['index']
        img, label, _ = dataset.vis_transform(imgs=imgs, labels=labels, preds=None)
        imshow(title='KiTS19', imgs=(img[0][1], label[0]))


if __name__ == '__main__':
    main()
