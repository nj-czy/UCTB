import os
import yaml
import argparse
import GPUtil
import numpy as np

from UCTB.dataset import TransferDataLoader
from UCTB.model import AMulti_GCLSTM
from UCTB.evaluation import metric

#####################################################################
# argument parser
parser = argparse.ArgumentParser(description="Argument Parser")
parser.add_argument('-m', '--model', default='amulti_gclstm_v4.model.yml')
parser.add_argument('-sd', '--source_data', default='bike_chicago.data.yml')
parser.add_argument('-td', '--target_data', default='bike_dc.data.yml')
parser.add_argument('-tdl', '--target_data_length', default='29', type=str)
parser.add_argument('-pt', '--pretrain', default='True')
parser.add_argument('-ft', '--finetune', default='True')
parser.add_argument('-tr', '--transfer', default='True')

args = vars(parser.parse_args())

with open(args['model'], 'r') as f:
    model_params = yaml.load(f)

with open(args['source_data'], 'r') as f:
    sd_params = yaml.load(f)

with open(args['target_data'], 'r') as f:
    td_params = yaml.load(f)

assert sd_params['closeness_len'] == td_params['closeness_len']
assert sd_params['period_len'] == td_params['period_len']
assert sd_params['trend_len'] == td_params['trend_len']

#####################################################################
# Generate code_version
group = 'Amulti_Transfer'
code_version = 'AMultiGCLSTM_SD_{}_TD_{}'.format(args['source_data'].split('.')[0].split('_')[-1],
                                                 args['target_data'].split('.')[0].split('_')[-1])

model_dir_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'model_dir')
model_dir_path = os.path.join(model_dir_path, group)
#####################################################################
# Config data loader

data_loader = TransferDataLoader(sd_params, td_params, model_params, td_data_length=args['target_data_length'])

deviceIDs = GPUtil.getAvailable(order='first', limit=2, maxLoad=0.3, maxMemory=0.3,
                                includeNan=False, excludeID=[], excludeUUID=[])

if len(deviceIDs) == 0:
    current_device = '-1'
else:
    current_device = str(deviceIDs[0])

sd_model = AMulti_GCLSTM(num_node=data_loader.sd_loader.station_number,
                         num_graph=data_loader.sd_loader.LM.shape[0],
                         external_dim=data_loader.sd_loader.external_dim,
                         tpe_dim=data_loader.sd_loader.tpe_dim,
                         code_version=code_version,
                         model_dir=model_dir_path,
                         gpu_device=current_device,
                         **sd_params, **model_params)
sd_model.build()

td_model = AMulti_GCLSTM(num_node=data_loader.td_loader.station_number,
                         num_graph=data_loader.td_loader.LM.shape[0],
                         external_dim=data_loader.td_loader.external_dim,
                         tpe_dim=data_loader.td_loader.tpe_dim,
                         code_version=code_version,
                         model_dir=model_dir_path,
                         gpu_device=current_device,
                         **td_params, **model_params)
td_model.build()

sd_de_normalizer = (lambda x: x) if sd_params['normalize'] is False \
                                else data_loader.sd_loader.normalizer.min_max_denormal
td_de_normalizer = (lambda x: x) if td_params['normalize'] is False \
                                else data_loader.td_loader.normalizer.min_max_denormal

print('#################################################################')
print('Source Domain information')
print(sd_params['dataset'], sd_params['city'])
print('Number of trainable variables', sd_model.trainable_vars)
print('Number of training samples', data_loader.sd_loader.train_sequence_len)

print('#################################################################')
print('Target Domain information')
print(td_params['dataset'], td_params['city'])
print('Number of trainable variables', td_model.trainable_vars)
print('Number of training samples', data_loader.td_loader.train_sequence_len)

pretrain_model_name = 'pretrain_C6'
finetune_model_name = 'finetune_C6_' + str(data_loader.td_loader.train_sequence_len)
transfer_model_name = 'transfer_C6_' + str(data_loader.td_loader.train_sequence_len)

if args['pretrain'] == 'True':
    print('#################################################################')
    print('Source Domain Pre-Train')

    try:
        sd_model.load(pretrain_model_name)
    except FileNotFoundError:
        sd_model.fit(closeness_feature=data_loader.sd_loader.train_closeness,
                     period_feature=data_loader.sd_loader.train_period,
                     trend_feature=data_loader.sd_loader.train_trend,
                     laplace_matrix=data_loader.sd_loader.LM,
                     target=data_loader.sd_loader.train_y,
                     external_feature=data_loader.sd_loader.train_ef,
                     sequence_length=data_loader.sd_loader.train_sequence_len,
                     output_names=('loss',),
                     evaluate_loss_name='loss',
                     op_names=('train_op',),
                     batch_size=sd_params['batch_size'],
                     max_epoch=sd_params['max_epoch'],
                     validate_ratio=0.1,
                     early_stop_method='t-test',
                     early_stop_length=sd_params['early_stop_length'],
                     early_stop_patience=sd_params['early_stop_patience'],
                     verbose=True,
                     save_model=False)
        sd_model.save(pretrain_model_name, global_step=0)

    prediction = sd_model.predict(closeness_feature=data_loader.sd_loader.test_closeness,
                                  period_feature=data_loader.sd_loader.test_period,
                                  trend_feature=data_loader.sd_loader.test_trend,
                                  laplace_matrix=data_loader.sd_loader.LM,
                                  target=data_loader.sd_loader.test_y,
                                  external_feature=data_loader.sd_loader.test_ef,
                                  output_names=('prediction',),
                                  sequence_length=data_loader.sd_loader.test_sequence_len,
                                  cache_volume=sd_params['batch_size'], )

    test_prediction = prediction['prediction']

    test_rmse, test_mape = metric.rmse(prediction=sd_de_normalizer(test_prediction),
                                       target=sd_de_normalizer(data_loader.sd_loader.test_y), threshold=0), \
                           metric.mape(prediction=sd_de_normalizer(test_prediction),
                                       target=sd_de_normalizer(data_loader.sd_loader.test_y), threshold=0)

    print('#################################################################')
    print('Source Domain Result')
    print(test_rmse, test_mape)

    td_model.load(pretrain_model_name)

    prediction = td_model.predict(closeness_feature=data_loader.td_loader.test_closeness,
                                  period_feature=data_loader.td_loader.test_period,
                                  trend_feature=data_loader.td_loader.test_trend,
                                  laplace_matrix=data_loader.td_loader.LM,
                                  target=data_loader.td_loader.test_y,
                                  external_feature=data_loader.td_loader.test_ef,
                                  output_names=('prediction',),
                                  sequence_length=data_loader.td_loader.test_sequence_len,
                                  cache_volume=td_params['batch_size'], )

    test_prediction = prediction['prediction']

    test_rmse, test_mape = metric.rmse(prediction=td_de_normalizer(test_prediction),
                                       target=td_de_normalizer(data_loader.td_loader.test_y), threshold=0), \
                           metric.mape(prediction=td_de_normalizer(test_prediction),
                                       target=td_de_normalizer(data_loader.td_loader.test_y), threshold=0)

    print('#################################################################')
    print('Target Domain Result')
    print(test_rmse, test_mape)

if args['finetune'] == 'True':
    try:
        td_model.load(finetune_model_name)
    except FileNotFoundError:
        td_model.load(pretrain_model_name)
        td_model.fit(closeness_feature=data_loader.td_loader.train_closeness,
                     period_feature=data_loader.td_loader.train_period,
                     trend_feature=data_loader.td_loader.train_trend,
                     laplace_matrix=data_loader.td_loader.LM,
                     target=data_loader.td_loader.train_y,
                     external_feature=data_loader.td_loader.train_ef,
                     sequence_length=data_loader.td_loader.train_sequence_len,
                     output_names=('loss',),
                     evaluate_loss_name='loss',
                     op_names=('train_op',),
                     batch_size=td_params['batch_size'],
                     max_epoch=td_params['max_epoch'],
                     validate_ratio=0.1,
                     early_stop_method='t-test',
                     early_stop_length=td_params['early_stop_length'],
                     early_stop_patience=td_params['early_stop_patience'],
                     verbose=True,
                     save_model=False)
        td_model.save(finetune_model_name, global_step=0)

    prediction = td_model.predict(closeness_feature=data_loader.td_loader.test_closeness,
                                  period_feature=data_loader.td_loader.test_period,
                                  trend_feature=data_loader.td_loader.test_trend,
                                  laplace_matrix=data_loader.td_loader.LM,
                                  target=data_loader.td_loader.test_y,
                                  external_feature=data_loader.td_loader.test_ef,
                                  output_names=('prediction',),
                                  sequence_length=data_loader.td_loader.test_sequence_len,
                                  cache_volume=td_params['batch_size'], )

    test_prediction = prediction['prediction']

    test_rmse, test_mape = metric.rmse(prediction=td_de_normalizer(test_prediction),
                                       target=td_de_normalizer(data_loader.td_loader.test_y), threshold=0), \
                           metric.mape(prediction=td_de_normalizer(test_prediction),
                                       target=td_de_normalizer(data_loader.td_loader.test_y), threshold=0)

    print('#################################################################')
    print('Target Domain Fine-tune')
    print(test_rmse, test_mape)

if args['transfer'] == 'True':

    try:
        td_model.load(transfer_model_name)
    except FileNotFoundError:
        td_model.load(pretrain_model_name)
        traffic_sim = data_loader.traffic_sim()

        # prepare data:
        feature_maps = []
        for record in traffic_sim:
            # score, index, start, end
            sd_transfer_data = data_loader.sd_loader.train_data[record[2]: record[3], :]

            transfer_closeness, \
            transfer_period, \
            transfer_trend, \
            _ = data_loader.sd_loader.st_move_sample.move_sample(sd_transfer_data)

            fm = sd_model.predict(closeness_feature=transfer_closeness,
                                  period_feature=transfer_period,
                                  trend_feature=transfer_trend,
                                  laplace_matrix=data_loader.sd_loader.LM,
                                  external_feature=data_loader.sd_loader.train_ef,
                                  output_names=['feature_map'],
                                  sequence_length=len(transfer_closeness),
                                  cache_volume=sd_params['batch_size'])

            feature_maps.append(fm['feature_map'][:, record[1]:record[1] + 1, :, :])

        feature_maps = np.concatenate(feature_maps, axis=1)

        # transfer
        td_model.fit(closeness_feature=data_loader.td_loader.train_closeness,
                     period_feature=data_loader.td_loader.train_period,
                     trend_feature=data_loader.td_loader.train_trend,
                     laplace_matrix=data_loader.td_loader.LM,
                     target=data_loader.td_loader.train_y,
                     external_feature=data_loader.td_loader.train_ef,
                     similar_feature_map=feature_maps,
                     sequence_length=data_loader.td_loader.train_sequence_len,
                     output_names=('transfer_loss',),
                     evaluate_loss_name='transfer_loss',
                     op_names=('transfer_op',),
                     batch_size=td_params['batch_size'],
                     max_epoch=td_params['max_epoch'],
                     validate_ratio=0.1,
                     early_stop_method='t-test',
                     early_stop_length=td_params['early_stop_length'],
                     early_stop_patience=td_params['early_stop_patience'],
                     verbose=True,
                     save_model=False)
        td_model.save(transfer_model_name, global_step=0)

    prediction = td_model.predict(closeness_feature=data_loader.td_loader.test_closeness,
                                  period_feature=data_loader.td_loader.test_period,
                                  trend_feature=data_loader.td_loader.test_trend,
                                  laplace_matrix=data_loader.td_loader.LM,
                                  target=data_loader.td_loader.test_y,
                                  external_feature=data_loader.td_loader.test_ef,
                                  output_names=('prediction',),
                                  sequence_length=data_loader.td_loader.test_sequence_len,
                                  cache_volume=td_params['batch_size'], )

    test_prediction = prediction['prediction']

    test_rmse, test_mape = metric.rmse(prediction=td_de_normalizer(test_prediction),
                                       target=td_de_normalizer(data_loader.td_loader.test_y), threshold=0), \
                           metric.mape(prediction=td_de_normalizer(test_prediction),
                                       target=td_de_normalizer(data_loader.td_loader.test_y), threshold=0)

    print('#################################################################')
    print('Target Domain Transfer')
    print(test_rmse, test_mape)