import pickle
import numpy as np
from PIL import Image
from dataloaders.dataset import cityscapes_base
from dataloaders.dataset import active_cityscapes
from collections import OrderedDict
from utils.cityscapes_to_lmdb import CITYSCAPES_IGNORE_INDEX
import constants
import os
from dataloaders import custom_transforms as tr


class RegionCityscapes(cityscapes_base.CityscapesBase):

    def __init__(self, path, base_size, crop_size, split, init_set, overfit=False):

        super(RegionCityscapes, self).__init__(path, base_size, crop_size, split, overfit)
        self.mode = active_cityscapes.Mode.ALL_BATCHES
        self.current_paths_to_regions = OrderedDict({})

        if self.split == 'train':

            with open(os.path.join(self.path, 'seed_sets', init_set), "r") as fptr:
                for path in fptr.readlines():
                    if path is not '':
                        path = u'{}'.format(path.strip()).encode('ascii')
                        self.current_paths_to_regions[path] = [(0, 0, crop_size, crop_size)]
                print(f'# of current_image_paths = {len(self.current_paths_to_regions.keys())}')

        else:
            for path in self.image_paths:
                self.current_paths_to_regions[path] = [(0, 0, crop_size, crop_size)]

        self.last_added_paths_to_regions = self.current_paths_to_regions.copy()

    def set_mode_all(self):
        self.mode = active_cityscapes.Mode.ALL_BATCHES

    def set_mode_last(self):
        self.mode = active_cityscapes.Mode.LAST_ADDED_BATCH

    def __len__(self):
        if self.mode == active_cityscapes.Mode.ALL_BATCHES:
            return len(self.current_paths_to_regions.keys())
        else:
            return len(self.last_added_paths_to_regions.keys())

    def __getitem__(self, index):

        img_path = None
        regions = None

        if self.mode == active_cityscapes.Mode.ALL_BATCHES:
            img_path, regions = list(self.current_paths_to_regions.items())[index]
        else:
            img_path, regions = list(self.last_added_paths_to_regions.items())[index]

        loaded_npy = None
        with self.env.begin(write=False) as txn:
            loaded_npy = pickle.loads(txn.get(img_path))

        image = loaded_npy[:, :, 0:3]
        target_full = loaded_npy[:, :, 3]

        target_masked = np.ones(target_full.shape, dtype=target_full.dtype) * CITYSCAPES_IGNORE_INDEX

        for r in regions:
            tr.invert_fix_scale_crop(target_full, target_masked, r, self.crop_size)

        sample = {'image': Image.fromarray(image), 'label': Image.fromarray(target_masked)}
        return self.get_transformed_sample(sample)


if __name__ == "__main__":

    from torch.utils.data import DataLoader
    import matplotlib.pyplot as plt
    from dataloaders.utils import map_segmentation_to_colors

    path = os.path.join(constants.DATASET_ROOT, 'cityscapes')
    crop_size = 513
    base_size = 513
    split = 'train'

    cityscapes_train = RegionCityscapes(path, base_size, crop_size, split, 'set_0.txt')
    dataloader = DataLoader(cityscapes_train, batch_size=2, shuffle=True, num_workers=0)

    for i, sample in enumerate(dataloader, 0):
        for j in range(sample['image'].size()[0]):
            image = sample['image'].numpy()
            gt = sample['label'].numpy()
            gt_colored = map_segmentation_to_colors(np.array(gt[j]).astype(np.uint8), 'cityscapes')
            image_unnormalized = ((np.transpose(image[j], axes=[1, 2, 0]) * (0.229, 0.224, 0.225) + (0.485, 0.456, 0.406)) * 255).astype(np.uint8)
            plt.figure()
            plt.title('display')
            plt.subplot(211)
            plt.imshow(image_unnormalized)
            plt.subplot(212)
            plt.imshow(gt_colored)

        if i == 1:
            break

    plt.show(block=True)
