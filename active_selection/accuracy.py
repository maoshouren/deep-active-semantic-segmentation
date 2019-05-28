from torch.utils.data import DataLoader
import torch
import numpy as np
from dataloaders.dataset import paths_dataset
from active_selection.base import ActiveSelectionBase
from tqdm import tqdm


class ActiveSelectionAccuracy(ActiveSelectionBase):

    def __init__(self, num_classes, dataset_lmdb_env, crop_size, dataloader_batch_size):
        super(ActiveSelectionAccuracy, self).__init__(dataset_lmdb_env, crop_size, dataloader_batch_size)
        self.num_classes = num_classes

    def get_least_accurate_sample_using_labels(self, model, images, selection_count):

        model.eval()
        loader = DataLoader(paths_dataset.PathsDataset(self.env, images, self.crop_size, include_labels=True),
                            batch_size=self.dataloader_batch_size, shuffle=False, num_workers=0)
        accuracy = []

        with torch.no_grad():
            for sample in tqdm(loader):
                image_batch = sample['image'].cuda()
                label_batch = sample['label'].cuda()
                output = model(image_batch)
                prediction = torch.argmax(output, dim=1).type(torch.cuda.FloatTensor)
                for idx in range(prediction.shape[0]):
                    mask = (label_batch[idx, :, :] >= 0) & (label_batch[idx, :, :] < self.num_classes)
                    correct = label_batch[idx, mask] == prediction[idx, mask]
                    accuracy.append(correct.sum().cpu().item() / (correct.shape[0]))

        selected_samples = list(zip(*sorted(zip(accuracy, images), key=lambda x: x[0], reverse=False)))[1][:selection_count]
        return selected_samples

    def get_least_accurate_samples(self, model, images, selection_count, mode='softmax'):

        model.eval()
        loader = DataLoader(paths_dataset.PathsDataset(self.env, images, self.crop_size),
                            batch_size=self.dataloader_batch_size, shuffle=False, num_workers=0)
        accuracy = []
        softmax = torch.nn.Softmax2d()
        with torch.no_grad():
            for sample in tqdm(loader):
                image_batch = sample.cuda()
                deeplab_output, unet_output = model(image_batch)
                if mode == 'softmax':
                    prediction = softmax(unet_output)
                    for idx in range(prediction.shape[0]):
                        accuracy.append(prediction[idx, 1, :, :].sum().cpu().item())
                elif mode == 'argmax':
                    prediction = unet_output.argmax(1).squeeze()
                    for idx in range(prediction.shape[0]):
                        accuracy.append(prediction[idx, :, :].sum().cpu().item())
                else:
                    raise NotImplementedError

        selected_samples = list(zip(*sorted(zip(accuracy, images), key=lambda x: x[0], reverse=False)))[1][:selection_count]
        return selected_samples
