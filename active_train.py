import argparse
import os
import numpy as np
from tqdm import tqdm
import math
import random

from dataloaders import make_dataloader
from models.sync_batchnorm.replicate import patch_replication_callback

from models.deeplab import *
from utils.loss import SegmentationLosses
from utils.calculate_weights import calculate_weights_labels
from utils.lr_scheduler import LR_Scheduler
from utils.saver import Saver, ActiveSaver
from utils.summaries import TensorboardSummary
from utils.metrics import Evaluator

import constants

import sys

class Trainer(object):

	def __init__(self, args, num_current_labeled_samples, dataloaders, overall_summary_writer):
		self.args = args

		self.saver = ActiveSaver(args, num_current_labeled_samples)
		self.saver.save_experiment_config()

		self.summary = TensorboardSummary(self.saver.experiment_dir)
		self.writer = self.summary.create_summary()

		self.overall_summary_writer = overall_summary_writer
		self.num_current_labeled_samples = num_current_labeled_samples

		self.train_loader, self.val_loader, self.test_loader, self.nclass = dataloaders

		model = DeepLab(num_classes=self.nclass, backbone=args.backbone, output_stride=args.out_stride, sync_bn=args.sync_bn, freeze_bn=args.freeze_bn)
		train_params = [{'params': model.get_1x_lr_params(), 'lr': args.lr},
						{'params': model.get_10x_lr_params(), 'lr': args.lr * 10}]
		
		optimizer = torch.optim.SGD(train_params, momentum=args.momentum, weight_decay=args.weight_decay, nesterov=args.nesterov)

		if args.use_balanced_weights:
			dataset_folder = args.dataset
			if args.dataset == 'active_cityscapes':
				dataset_folder = 'cityscapes'

			classes_weights_path = os.path.join(constants.DATASET_ROOT, dataset_folder, 'class_weights.npy')
			if os.path.isfile(classes_weights_path):
				weight = np.load(classes_weights_path)
			else:
				weight = calculate_weights_labels(args.dataset, self.train_loader, self.nclass)
			weight = torch.from_numpy(weight.astype(np.float32))
		else:
			weight = None

		self.criterion = SegmentationLosses(weight=weight, cuda=args.cuda).build_loss(mode=args.loss_type)
		self.model, self.optimizer = model, optimizer

		self.evaluator = Evaluator(self.nclass)

		self.scheduler = LR_Scheduler(args.lr_scheduler, args.lr, args.epochs, len(self.train_loader))

		if args.cuda:
			self.model = torch.nn.DataParallel(self.model, device_ids=self.args.gpu_ids)
			patch_replication_callback(self.model)
			self.model = self.model.cuda()

		self.best_pred = 0.0
		if args.resume is not None:
			if not os.path.isfile(args.resume):
				raise RuntimeError(f"=> no checkpoint found at {args.resume}")
			checkpoint = torch.load(args.resume)
			args.start_epoch = checkpoint['epoch']
			if args.cuda:
				self.model.module.load_state_dict(checkpoint['state_dict'])
			else:
				self.model.load_state_dict(checkpoint['state_dict'])
			if not args.ft:
				self.optimizer.load_state_dict(checkpoint['optimizer'])
			self.best_pred = checkpoint['best_pred']
			print(f'=> loaded checkpoint {args.resume} (epoch {checkpoint["epoch"]})')


	def training(self, epoch):

		train_loss = 0.0
		self.model.train()
		num_img_tr = len(self.train_loader)
		tbar = tqdm(self.train_loader, desc='\r')

		for i, sample in enumerate(tbar):
			image, target = sample['image'], sample['label']

			if self.args.cuda:
				image, target = image.cuda(), target.cuda()

			self.scheduler(self.optimizer, i, epoch, self.best_pred)
			self.optimizer.zero_grad()
			output = self.model(image)
			loss = self.criterion(output, target)
			loss.backward()
			self.optimizer.step()
			train_loss += loss.item()
			tbar.set_description('Train loss: %.3f' % (train_loss / (i + 1)))
			self.writer.add_scalar('train/total_loss_iter', loss.item(), i + num_img_tr * epoch)

			if num_img_tr >= constants.TENSORBOARD_VISUALIZATION_INTERVAL:
				if i % (num_img_tr // constants.TENSORBOARD_VISUALIZATION_INTERVAL) == 0:
					global_step = i + num_img_tr * epoch
					self.summary.visualize_image(self.writer, self.args.dataset, image, target, output, global_step)

		self.writer.add_scalar('train/total_loss_epoch', train_loss, epoch)
		print('[Epoch: %d, numImages: %5d]' % (epoch, i * self.args.batch_size + image.data.shape[0]))
		print('Loss: %.3f' % train_loss)
		print('BestPred: %.3f' % self.best_pred)

		if self.args.no_val:
			# save checkpoint every epoch
			is_best = False
			self.saver.save_checkpoint({
				'epoch': epoch + 1,
				'state_dict': self.model.module.state_dict(),
				'optimizer': self.optimizer.state_dict(),
				'best_pred': self.best_pred,
			}, is_best)

		return train_loss


	def validation(self, epoch):

		self.model.eval()
		self.evaluator.reset()

		tbar = tqdm(self.val_loader, desc='\r')
		test_loss = 0.0

		for i, sample in enumerate(tbar):
			image, target = sample['image'], sample['label']

			if self.args.cuda:
				image, target = image.cuda(), target.cuda()

			with torch.no_grad():
				output = self.model(image)

			loss = self.criterion(output, target)
			test_loss += loss.item()
			tbar.set_description('Test loss: %.3f' % (test_loss / (i + 1)))
			pred = output.data.cpu().numpy()
			target = target.cpu().numpy()
			pred = np.argmax(pred, axis=1)
			self.evaluator.add_batch(target, pred)

		# Fast test during the training
		Acc = self.evaluator.Pixel_Accuracy()
		Acc_class = self.evaluator.Pixel_Accuracy_Class()
		mIoU = self.evaluator.Mean_Intersection_over_Union()
		FWIoU = self.evaluator.Frequency_Weighted_Intersection_over_Union()
		self.writer.add_scalar('val/total_loss_epoch', test_loss, epoch)
		self.writer.add_scalar('val/mIoU', mIoU, epoch)
		self.writer.add_scalar('val/Acc', Acc, epoch)
		self.writer.add_scalar('val/Acc_class', Acc_class, epoch)
		self.writer.add_scalar('val/fwIoU', FWIoU, epoch)
		print('Validation:')
		print('[Epoch: %d, numImages: %5d]' % (epoch, i * self.args.batch_size + image.data.shape[0]))
		print("Acc:{}, Acc_class:{}, mIoU:{}, fwIoU: {}".format(Acc, Acc_class, mIoU, FWIoU))
		print('Loss: %.3f' % test_loss)
		
		new_pred = mIoU
		is_best = False
		if new_pred > self.best_pred:
			is_best = True
			self.best_pred = new_pred

		# save every validation model (overwrites)
		self.saver.save_checkpoint({
			'epoch': epoch + 1,
			'state_dict': self.model.module.state_dict(),
			'optimizer': self.optimizer.state_dict(),
			'best_pred': self.best_pred,
		}, is_best)

		return test_loss, mIoU, Acc, Acc_class, FWIoU


def main():
	
	parser = argparse.ArgumentParser(description="PyTorch DeeplabV3Plus Training")
	parser.add_argument('--backbone', type=str, default='resnet',
						choices=['resnet', 'xception', 'drn', 'mobilenet'],
						help='backbone name (default: resnet)')
	parser.add_argument('--out-stride', type=int, default=16,
						help='network output stride (default: 8)')
	parser.add_argument('--dataset', type=str, default='active_cityscapes',
						choices=['pascal', 'coco', 'active_cityscapes'],
						help='dataset name (default: active_cityscapes)')
	parser.add_argument('--use-sbd', action='store_true', default=False,
						help='whether to use SBD dataset (default: False)')
	parser.add_argument('--workers', type=int, default=4,
						metavar='N', help='dataloader threads')
	parser.add_argument('--base-size', type=int, default=513,
						help='base image size')
	parser.add_argument('--crop-size', type=int, default=513,
						help='crop image size')
	parser.add_argument('--sync-bn', type=bool, default=None,
						help='whether to use sync bn (default: auto)')
	parser.add_argument('--freeze-bn', type=bool, default=False,
						help='whether to freeze bn parameters (default: False)')
	parser.add_argument('--loss-type', type=str, default='ce',
						choices=['ce', 'focal'],
						help='loss func type (default: ce)')
	# training hyper params
	parser.add_argument('--epochs', type=int, default=None, metavar='N',
						help='number of epochs to train (default: auto)')
	parser.add_argument('--start_epoch', type=int, default=0,
						metavar='N', help='start epochs (default:0)')
	parser.add_argument('--batch-size', type=int, default=None,
						metavar='N', help='input batch size for \
								training (default: auto)')
	parser.add_argument('--test-batch-size', type=int, default=None,
						metavar='N', help='input batch size for \
								testing (default: auto)')
	parser.add_argument('--use-balanced-weights', action='store_true', default=False,
						help='whether to use balanced weights (default: False)')
	# optimizer params
	parser.add_argument('--lr', type=float, default=None, metavar='LR',
						help='learning rate (default: auto)')
	parser.add_argument('--lr-scheduler', type=str, default='poly',
						choices=['poly', 'step', 'cos'],
						help='lr scheduler mode: (default: poly)')
	parser.add_argument('--momentum', type=float, default=0.9,
						metavar='M', help='momentum (default: 0.9)')
	parser.add_argument('--weight-decay', type=float, default=5e-4,
						metavar='M', help='w-decay (default: 5e-4)')
	parser.add_argument('--nesterov', action='store_true', default=False,
						help='whether use nesterov (default: False)')
	# cuda, seed and logging
	parser.add_argument('--no-cuda', action='store_true', default=
						False, help='disables CUDA training')
	parser.add_argument('--gpu-ids', type=str, default='0',
						help='use which gpu to train, must be a \
						comma-separated list of integers only (default=0)')
	parser.add_argument('--seed', type=int, default=1, metavar='S',
						help='random seed (default: 1)')
	# checking point
	parser.add_argument('--resume', type=str, default=None,
						help='put the path to resuming file if needed')
	parser.add_argument('--checkname', type=str, default=None,
						help='set the checkpoint name')
	# finetuning pre-trained models
	parser.add_argument('--ft', action='store_true', default=False,
						help='finetuning on a different dataset')
	# evaluation option
	parser.add_argument('--eval-interval', type=int, default=1,
						help='evaluuation interval (default: 1)')
	parser.add_argument('--no-val', action='store_true', default=False,
						help='skip validation during training')
	parser.add_argument('--overfit', action='store_true', default=False,
						help='overfit to one sample')
	parser.add_argument('--seed_set', action='store_true', default='set_0.txt',
						help='initial labeled set')
	parser.add_argument('--active-batch-size', action='store_true', default=50,
						help='batch size queried from oracle')

	args = parser.parse_args()
	args.cuda = not args.no_cuda and torch.cuda.is_available()
	if args.cuda:
		try:
			args.gpu_ids = [int(s) for s in args.gpu_ids.split(',')]
		except ValueError:
			raise ValueError('Argument --gpu_ids must be a comma-separated list of integers only')

	if args.sync_bn is None:
		if args.cuda and len(args.gpu_ids) > 1:
			args.sync_bn = True
		else:
			args.sync_bn = False

	# default settings for epochs, batch_size and lr
	if args.epochs is None:
		epoches = {
			'coco': 30,
			'cityscapes': 200,
			'active_cityscapes': 50,
			'pascal': 50,
		}
		args.epochs = epoches[args.dataset.lower()]

	if args.batch_size is None:
		args.batch_size = 4 * len(args.gpu_ids)

	if args.test_batch_size is None:
		args.test_batch_size = args.batch_size

	if args.lr is None:
		lrs = {
			'coco': 0.1,
			'cityscapes': 0.01,
			'active_cityscapes': 0.01,
			'pascal': 0.007,
		}
		args.lr = lrs[args.dataset.lower()] / (4 * len(args.gpu_ids)) * args.batch_size


	if args.checkname is None:
		args.checkname = 'deeplab-'+str(args.backbone)
	
	print()
	print(args)
	torch.manual_seed(args.seed)
	
	kwargs = {'num_workers': args.workers, 'pin_memory': True, 'init_set': args.seed_set}
	dataloaders = make_dataloader(args.dataset, args.base_size, args.crop_size, args.batch_size, args.overfit, **kwargs)
	training_set = dataloaders[0]
	dataloaders = dataloaders[1:]

	saver = Saver(args, remove_existing=True)
	saver.save_experiment_config()
	summary = TensorboardSummary(saver.experiment_dir)
	writer = summary.create_summary()
	
	active_selection_function = lambda x: random.random()

	total_active_selection_iterations = training_set.count_expands_needed(args.active_batch_size)

	print()

	for selection_iter in range(total_active_selection_iterations):
		print(f'ActiveIteration-{selection_iter:03d}/{total_active_selection_iterations:03d} [{len(training_set):04d}/{len(training_set.remaining_image_paths):04d}/{training_set.count_expands_needed(args.active_batch_size):03d}]')
		trainer = Trainer(args, len(training_set), dataloaders, writer)
		train_loss = math.inf

		for epoch in range(trainer.args.start_epoch, trainer.args.epochs):
			train_loss = trainer.training(epoch)
			if not trainer.args.no_val and epoch % args.eval_interval == (args.eval_interval - 1):
				trainer.validation(epoch)
		
		test_loss, mIoU, Acc, Acc_class, FWIoU = trainer.validation(trainer.args.epochs - 1)
		
		writer.add_scalar('train/total_loss', train_loss, len(training_set))
		writer.add_scalar('val/total_loss', test_loss, len(training_set))
		writer.add_scalar('val/mIoU', mIoU, len(training_set))
		writer.add_scalar('val/Acc', Acc, len(training_set))
		writer.add_scalar('val/Acc_class', Acc_class, len(training_set))
		writer.add_scalar('val/fwIoU', FWIoU, len(training_set))
		
		trainer.writer.close()
		training_set.expand_training_set(active_selection_function, args.active_batch_size)
	
	writer.close()

if __name__ == "__main__":
   main()