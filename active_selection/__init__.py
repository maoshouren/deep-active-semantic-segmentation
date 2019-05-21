from active_selection.ceal import ActiveSelectionCEAL
from active_selection.core_set import ActiveSelectionCoreSet
from active_selection.mc_dropout import ActiveSelectionMCDropout
from active_selection.max_subset import ActiveSelectionMaxSubset
from active_selection.mc_noise import ActiveSelectionMCNoise
from active_selection.accuracy import ActiveSelectionAccuracy


def get_active_selection_class(active_selection_method, dataset_num_classes, dataset_lmdb_env, crop_size, dataloader_batch_size):
    if active_selection_method == 'coreset':
        return ActiveSelectionCoreSet(dataset_lmdb_env, crop_size, dataloader_batch_size)
    elif active_selection_method == 'ceal_confidence' or active_selection_method == 'ceal_margin' or active_selection_method == 'ceal_entropy' or active_selection_method == 'ceal_fusion' or active_selection_method == 'ceal_entropy_weakly_labeled':
        return ActiveSelectionCEAL(dataset_lmdb_env, crop_size, dataloader_batch_size)
    elif active_selection_method == 'noise_image' or active_selection_method == 'noise_feature' or active_selection_method == 'noise_variance':
        return ActiveSelectionMCNoise(dataset_num_classes, dataset_lmdb_env, crop_size, dataloader_batch_size)
    elif active_selection_method == 'variance' or active_selection_method == 'variance_representative':
        return ActiveSelectionMCDropout(dataset_num_classes, dataset_lmdb_env, crop_size, dataloader_batch_size)
    elif active_selection_method == 'accuracy_labels':
        return ActiveSelectionAccuracy(dataset_num_classes, dataset_lmdb_env, crop_size, dataloader_batch_size)
    else:
        raise NotImplementedError


def get_max_subset_active_selector(dataset_lmdb_env, crop_size, dataloader_batch_size):
    return ActiveSelectionMaxSubset(dataset_lmdb_env, crop_size, dataloader_batch_size)
