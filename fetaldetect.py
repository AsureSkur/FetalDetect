# -*- coding: utf-8 -*-
"""
Created on Sat Jul 11 20:03:36 2020

@author: Meihaonan
"""
import os
import paddle
import numpy as np
import paddle.fluid as fluid
from multiprocessing import cpu_count
import matplotlib.pyplot as plt

def to_image(L):            #将1*2400连续胎心率数据点转化成120*2400胎心率曲线图
    image=np.zeros([120,2400],dtype=np.int)
    #np.zero(120,2400)好像有点问题
    for i in range(2400):
        if(L[i]<80):
            L[i]=80
        if(L[i]>=200):
            L[i]=199
        image[L[i]-80][i]=1
    return image

def data_reader(data,label,buffered_size=512):
  def reader():
      x=data.shape[0]
      for i in range(0,x):
          image=to_image(data[i,:])
          yield image,[label[i]-1]  #算交叉熵label要从0开始
  return reader
  
train_data=np.loadtxt("heart_data/train_data.txt",
                      delimiter=" ",dtype=int)
train_data_label=np.loadtxt("heart_data/train_label.txt",
                            delimiter=" ",dtype=int)

test_data=np.loadtxt("heart_data/test_data.txt",
                     delimiter=" ",dtype=int)
test_data_label=np.loadtxt("heart_data/test_label.txt",
                           delimiter=" ",dtype=int)

#构造训练、测试数据提供器
BATCH_SIZE = 16
train_r =data_reader(train_data,train_data_label)
train_reader =paddle.batch(paddle.reader.shuffle(reader=train_r,buf_size=128),
                          batch_size=BATCH_SIZE)

test_r=data_reader(test_data,test_data_label)
test_reader = paddle.batch(test_r,batch_size=BATCH_SIZE)

def mknet(input,type_size): #输入图片，分出的类别
    def conv_block(ipt, num_filter, groups, dropouts):
        return fluid.nets.img_conv_group(
            input=ipt,
            pool_size=2,
            pool_stride=2,
            conv_num_filter=[num_filter] * groups,
            conv_filter_size=3,
            conv_act='relu',
            conv_with_batchnorm=True,
            conv_batchnorm_drop_rate=dropouts,
            pool_type='max')

    conv1 = conv_block(input, 64, 2, [0, 0])
    conv2 = conv_block(conv1, 128, 2, [0, 0])
    conv3 = conv_block(conv2, 256, 3, [0, 0, 0])
    conv4 = conv_block(conv3, 512, 3, [0, 0, 0])
    conv5 = conv_block(conv4, 512, 3, [0, 0, 0])

    drop = fluid.layers.dropout(x=conv5, dropout_prob=0.5)
    fc1 = fluid.layers.fc(input=drop, size=512, act=None)
    bn = fluid.layers.batch_norm(input=fc1, act='relu')
    drop2 = fluid.layers.dropout(x=bn, dropout_prob=0.5)
    fc2 = fluid.layers.fc(input=drop2, size=512, act=None)
    predict = fluid.layers.fc(input=fc2, size=type_size, act='softmax')
    return predict

# 定义输入输出层
# 定义两个张量
image =fluid.layers.data(name='image', shape=[1, 120, 2400], dtype='float32') 
label =fluid.layers.data(name='label', shape=[1], dtype='int64')

# 获取分类器
predict=mknet(image,3) #分成3类

# 定义损失函数和准确率函数
cost =fluid.layers.cross_entropy(input=predict, label=label)
avg_cost =fluid.layers.mean(cost)
accuracy =fluid.layers.accuracy(input=predict, label=label) 

# 克隆main_program得到test_program，使用参数for_test来区分该程序是用来训练还是用来测试
# 该fluid.default_main_program().clone()要在optimization之前使用.
test_program =fluid.default_main_program().clone(for_test=True)

# 使用Adam优化器，定学习率为0.002。
optimizer = fluid.optimizer.AdamOptimizer(learning_rate=0.002)  
opts = optimizer.minimize(avg_cost)

#定义使用CPU还是GPU，使用CPU时use_cuda = False,使用GPU时use_cuda = True
use_cuda = False
place = fluid.CUDAPlace(0) if use_cuda else fluid.CPUPlace()
#创建一个Executor实例exe
exe =fluid.Executor(place)
#正式进行网络训练前，需先执行参数初始化
exe.run(fluid.default_startup_program())

feeder = fluid.DataFeeder(place=place, feed_list=[image, label])

EPOCH_NUM = 60
#训练过程数据记录
all_train_iter=0
all_train_iters=[]
all_train_costs=[]
all_train_accs=[]


#测试过程数据记录
all_test_iter=0
all_test_iters=[]
all_test_costs=[]
all_test_accs=[]
model_save_dir ="work"


for pass_id in range(EPOCH_NUM):
    # 开始训练
    for batch_id, data in enumerate(train_reader()):      #遍历训练集，并为数据加上索引batch_id
        train_cost,train_acc =exe.run(program=fluid.default_main_program(),#运行主程序
                             feed=feeder.feed(data),                 #喂入一个batch的数据
                            fetch_list=[avg_cost, accuracy])         #fetch均方误差和准确率
        all_train_iter=all_train_iter+BATCH_SIZE
        all_train_iters.append(all_train_iter)
        all_train_costs.append(train_cost[0])
        all_train_accs.append(train_acc[0])
        #每10次batch打印一次训练、进行一次测试
        if batch_id % 10== 0:                                            
            print('Pass:%d, Batch:%d,Cost:%0.5f, Accuracy:%0.5f' %
            (pass_id, batch_id, train_cost[0],train_acc[0]))
    # 开始测试
    test_costs = []                                                        #测试的损失值
    test_accs = []                                                         #测试的准确率
    for batch_id, data in enumerate(test_reader()):
        test_cost, test_acc =exe.run(program=test_program,              #执行训练程序
                                     feed=feeder.feed(data),            #喂入数据
                                     fetch_list=[avg_cost, accuracy])         #fetch 误差、准确率
        test_costs.append(test_cost[0])                                #记录每个batch的误差
        test_accs.append(test_acc[0])                             #记录每个batch的准确率
        all_test_iter=all_test_iter+BATCH_SIZE
        all_test_iters.append(all_test_iter)
        all_test_costs.append(test_cost[0])                                      
        all_test_accs.append(test_acc[0])
# 求测试结果的平均值
    test_cost = (sum(test_costs) /len(test_costs))        #计算误差平均值（误差和/误差的个数）
    test_acc = (sum(test_accs) /len(test_accs))  #计算准确率平均值（ 准确率的和/准确率的个数）
    print('Test:%d, Cost:%0.5f, ACC:%0.5f' %(pass_id, test_cost, test_acc))

# 每轮训练完成后，对模型进行一次保存，使用飞桨提供的fluid.io.save_inference_model()进行模型保存：
# 保存模型
# 如果保存路径不存在就创建
if not os.path.exists(model_save_dir):
    os.makedirs(model_save_dir)
print('savemodels to %s' % (model_save_dir))
fluid.io.save_inference_model(model_save_dir,  # 保存预测Program的路径
                              ['image'],      #预测需要feed的数据
                              [predict],       #保存预测结果
                              exe)             #executor 保存预测模型

def draw_cost_process(title,iters,costs,label_cost):
    plt.title(title, fontsize=24)
    plt.xlabel("iter", fontsize=20)
    plt.ylabel("cost", fontsize=20)
    plt.plot(iters,costs,color='red',label=label_cost)
    plt.legend()
    plt.grid()
    plt.show()
    
def draw_acc_process(title,iters,acc,label_acc):
    plt.title(title, fontsize=24)
    plt.xlabel("iter", fontsize=20)
    plt.ylabel("acc", fontsize=20)
    plt.plot(iters,acc,color='green',label=label_acc)
    plt.legend()
    plt.grid()
    plt.show()
    
#调用绘制曲线
draw_acc_process("training",all_train_iters, all_train_accs, "trainning acc")
draw_acc_process("testing",all_test_iters, all_test_accs, "test acc")
draw_cost_process("training",all_train_iters, all_train_costs, "trainning acc")
draw_cost_process("testing",all_test_iters, all_test_costs, "test acc")



