import torch
import torch.nn as nn
import torch.nn.functional as F
from models.sync_batchnorm.batchnorm import SynchronizedBatchNorm2d
from models.aspp import ASPP
from models.decoder import Decoder
from models.backbone import build_backbone


class DeepLab(nn.Module):

    def __init__(self, backbone='mobilenet', output_stride=16, num_classes=19, sync_bn=True, freeze_bn=False, mc_dropout=False, return_features=False, average_pool_kernel_size=(65, 65)):

        super(DeepLab, self).__init__()

        if sync_bn == True:
            batchnorm = SynchronizedBatchNorm2d
        else:
            batchnorm = nn.BatchNorm2d

        self.average_pool_kernel_size = average_pool_kernel_size
        self.average_pool_stride = self.average_pool_kernel_size[0] // 2
        self.return_features = return_features
        self.backbone = build_backbone(backbone, output_stride, batchnorm, mc_dropout)
        self.aspp = ASPP(backbone, output_stride, batchnorm)
        self.decoder = Decoder(num_classes, backbone, batchnorm, mc_dropout)

        if freeze_bn:
            self.freeze_bn()

    def set_return_features(self, return_features):
        self.return_features = return_features

    def forward(self, input):

        x, low_level_feat = self.backbone(input)
        x = self.aspp(x)
        low_res_x, features = self.decoder(x, low_level_feat)
        x = F.interpolate(low_res_x, size=input.size()[2:], mode='bilinear', align_corners=True)
        if self.return_features:
            return x, F.avg_pool2d(features, self.average_pool_kernel_size, self.average_pool_stride)
        return x

    def freeze_bn(self):
        for m in self.modules():
            if isinstance(m, SynchronizedBatchNorm2d):
                m.eval()
            elif isinstance(m, nn.BatchNorm2d):
                m.eval()

    def get_1x_lr_params(self):
        modules = [self.backbone]
        for i in range(len(modules)):
            for m in modules[i].named_modules():
                if isinstance(m[1], nn.Conv2d) or isinstance(m[1], SynchronizedBatchNorm2d) \
                        or isinstance(m[1], nn.BatchNorm2d):
                    for p in m[1].parameters():
                        if p.requires_grad:
                            yield p

    def get_10x_lr_params(self):
        modules = [self.aspp, self.decoder]
        for i in range(len(modules)):
            for m in modules[i].named_modules():
                if isinstance(m[1], nn.Conv2d) or isinstance(m[1], SynchronizedBatchNorm2d) \
                        or isinstance(m[1], nn.BatchNorm2d):
                    for p in m[1].parameters():
                        if p.requires_grad:
                            yield p


if __name__ == "__main__":

    model = DeepLab(backbone='mobilenet', output_stride=16, mc_dropout=True)
    model.eval()

    def turn_on_dropout(m):
        if type(m) == nn.Dropout2d:
            m.train()

    model.apply(turn_on_dropout)

    input = torch.rand(1, 3, 513, 513)
    output = model(input)
    print(output.size())
    print('NumElements: ', sum([p.numel() for p in model.parameters()]))

    model = DeepLab(backbone='mobilenet', output_stride=16, mc_dropout=True, return_features=True)
    model.eval()
    input = torch.rand(1, 3, 513, 513)
    output, features = model(input)
    print(features.size())
