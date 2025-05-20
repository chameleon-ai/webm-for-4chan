# Note: This stuff was taken from UVR.py and bastardized to work for one specific model.
# It's VERY likely that other models don't work, especially anything outside MDX_NET.
# The main problem with running separate.py is the ModelData() class, which is entangled
# with the 'root' GUI window object. Below is a copy of ModelData with root sloppily removed.
# In addition to ModelData, the minimum amount of helper functions and global definitions
# from UVR.py was also copied over. uvr_separate() is based on how UVR.py uses separate.
# This module assumes that it's one directory up from ultimatevocalremovergui

import hashlib
import json
import os
import sys
import yaml
from ml_collections import ConfigDict
from huggingface_hub import hf_hub_download
# Need to add uvr to module search path
sys.path.append(os.path.join(os.path.dirname(__file__), 'ultimatevocalremovergui'))
from .ultimatevocalremovergui.gui_data.constants import *
from .ultimatevocalremovergui.separate import (
    SeperateMDXC, clear_gpu_cache
)
MDX_MODELS_DIR = './models'
MDX_HASH_DIR = './advanced/ultimatevocalremovergui/models/MDX_Net_Models/model_data'
MDX_HASH_JSON = os.path.join(MDX_HASH_DIR, 'model_data.json')
MDX_C_CONFIG_PATH = os.path.join(MDX_HASH_DIR, 'mdx_c_configs')
model_hash_table = {}
def load_model_hash_data(dictionary):
    '''Get the model hash dictionary'''
    with open(dictionary, 'r') as d:
        return json.load(d)
mdx_hash_MAPPER = load_model_hash_data(MDX_HASH_JSON)

class ModelData():
    def __init__(self, model_name: str, 
                 selected_process_method=ENSEMBLE_MODE, 
                 is_secondary_model=False, 
                 primary_model_primary_stem=None, 
                 is_primary_model_primary_stem_only=False, 
                 is_primary_model_secondary_stem_only=False, 
                 is_pre_proc_model=False,
                 is_dry_check=False,
                 is_change_def=False,
                 is_get_hash_dir_only=False,
                 is_vocal_split_model=False):

        device_set = DEFAULT
        self.DENOISER_MODEL = None
        self.DEVERBER_MODEL = None
        self.is_deverb_vocals = False
        self.deverb_vocal_opt = None #DEVERB_MAPPER[]
        self.is_denoise_model = False
        self.is_gpu_conversion = 0
        self.is_normalization = False
        self.is_use_opencl = False#True if is_opencl_only else root.is_use_opencl_var.get()
        self.is_primary_stem_only = False
        self.is_secondary_stem_only = False
        self.is_denoise = False
        self.is_mdx_c_seg_def = 512
        self.mdx_batch_size = 1
        self.mdxnet_stem_select = VOCAL_STEM
        self.overlap = 0.25
        self.overlap_mdx = 0.25
        self.overlap_mdx23 = int(12)
        self.semitone_shift = float(0.0)
        self.is_pitch_change = False if self.semitone_shift == 0 else True
        self.is_match_frequency_pitch = False
        self.is_mdx_ckpt = False
        self.is_mdx_c = False
        self.is_mdx_combine_stems = False
        self.mdx_c_configs = None
        self.mdx_model_stems = []
        self.mdx_dim_f_set = None
        self.mdx_dim_t_set = None
        self.mdx_stem_count = 1
        self.compensate = None
        self.mdx_n_fft_scale_set = None
        self.wav_type_set = 'PCM_32'
        self.device_set = device_set.split(':')[-1].strip() if ':' in device_set else device_set
        self.mp3_bit_set = '120k'
        self.save_format = MP3 # root.save_format_var.get()
        self.is_invert_spec = None # root.is_invert_spec_var.get()#
        self.is_mixer_mode = False#
        self.demucs_stems = None # root.demucs_stems_var.get()
        self.is_demucs_combine_stems = False # root.is_demucs_combine_stems_var.get()
        self.demucs_source_list = []
        self.demucs_stem_count = 0
        self.mixer_path = None # MDX_MIXER_PATH
        self.model_name = model_name
        self.process_method = selected_process_method
        self.model_status = False if self.model_name == CHOOSE_MODEL or self.model_name == NO_MODEL else True
        self.primary_stem = None
        self.secondary_stem = None
        self.primary_stem_native = None
        self.is_ensemble_mode = False
        self.ensemble_primary_stem = None
        self.ensemble_secondary_stem = None
        self.primary_model_primary_stem = primary_model_primary_stem
        self.is_secondary_model = True if is_vocal_split_model else is_secondary_model
        self.secondary_model = None
        self.secondary_model_scale = None
        self.demucs_4_stem_added_count = 0
        self.is_demucs_4_stem_secondaries = False
        self.is_4_stem_ensemble = False
        self.pre_proc_model = None
        self.pre_proc_model_activated = False
        self.is_pre_proc_model = is_pre_proc_model
        self.is_dry_check = is_dry_check
        self.model_samplerate = 44100
        self.model_capacity = 32, 128
        self.is_vr_51_model = False
        self.is_demucs_pre_proc_model_inst_mix = False
        self.manual_download_Button = None
        self.secondary_model_4_stem = []
        self.secondary_model_4_stem_scale = []
        self.secondary_model_4_stem_names = []
        self.secondary_model_4_stem_model_names_list = []
        self.all_models = []
        self.secondary_model_other = None
        self.secondary_model_scale_other = None
        self.secondary_model_bass = None
        self.secondary_model_scale_bass = None
        self.secondary_model_drums = None
        self.secondary_model_scale_drums = None
        self.is_multi_stem_ensemble = False
        self.is_karaoke = False
        self.is_bv_model = False
        self.bv_model_rebalance = 0
        self.is_sec_bv_rebalance = False
        self.is_change_def = is_change_def
        self.model_hash_dir = None
        self.is_get_hash_dir_only = is_get_hash_dir_only
        self.is_secondary_model_activated = False
        self.vocal_split_model = None
        self.is_vocal_split_model = is_vocal_split_model
        self.is_vocal_split_model_activated = False
        self.is_save_inst_vocal_splitter = False # root.is_save_inst_set_vocal_splitter_var.get()
        self.is_inst_only_voc_splitter = False # root.check_only_selection_stem(INST_STEM_ONLY)
        self.is_save_vocal_only = False

        self.process_method = MDX_ARCH_TYPE     
        if self.process_method == MDX_ARCH_TYPE:
            self.is_secondary_model_activated = False # root.mdx_is_secondary_model_activate_var.get() if not is_secondary_model else False
            self.margin = 0 # int(root.margin_var.get())
            self.chunks = 0
            self.mdx_segment_size = 512 # int(root.mdx_segment_size_var.get())
            self.get_mdx_model_path()
            self.get_model_hash()
            if self.model_hash:
                self.model_hash_dir = os.path.join(MDX_HASH_DIR, f"{self.model_hash}.json")
                if is_change_def:
                    self.model_data = self.change_model_data()
                else:
                    self.model_data = self.get_model_data(MDX_HASH_DIR, mdx_hash_MAPPER)
                if self.model_data:
                    
                    if "config_yaml" in self.model_data:
                        self.is_mdx_c = True
                        config_path = os.path.join(MDX_C_CONFIG_PATH, self.model_data["config_yaml"])
                        if os.path.isfile(config_path):
                            with open(config_path) as f:
                                config = ConfigDict(yaml.load(f, Loader=yaml.FullLoader))

                            self.mdx_c_configs = config
                                
                            if self.mdx_c_configs.training.target_instrument:
                                # Use target_instrument as the primary stem and set 4-stem ensemble to False
                                target = self.mdx_c_configs.training.target_instrument
                                self.mdx_model_stems = [target]
                                self.primary_stem = target
                            else:
                                # If no specific target_instrument, use all instruments in the training config
                                self.mdx_model_stems = self.mdx_c_configs.training.instruments
                                self.mdx_stem_count = len(self.mdx_model_stems)
                                
                                # Set primary stem based on stem count
                                if self.mdx_stem_count == 2:
                                    self.primary_stem = self.mdx_model_stems[0]
                                else:
                                    self.primary_stem = self.mdxnet_stem_select
                                
                                # Update mdxnet_stem_select based on ensemble mode
                                if self.is_ensemble_mode:
                                    self.mdxnet_stem_select = self.ensemble_primary_stem
                        else:
                            self.model_status = False
                    else:
                        self.compensate = self.model_data["compensate"]
                        self.mdx_dim_f_set = self.model_data["mdx_dim_f_set"]
                        self.mdx_dim_t_set = self.model_data["mdx_dim_t_set"]
                        self.mdx_n_fft_scale_set = self.model_data["mdx_n_fft_scale_set"]
                        self.primary_stem = self.model_data["primary_stem"]
                        self.primary_stem_native = self.model_data["primary_stem"]
                        self.check_if_karaokee_model()
                        
                    self.secondary_stem = secondary_stem(self.primary_stem)
                else:
                    self.model_status = False
            
        if self.model_status:
            self.model_basename = os.path.splitext(os.path.basename(self.model_path))[0]
        else:
            self.model_basename = None
            
        self.pre_proc_model_activated = self.pre_proc_model_activated if not self.is_secondary_model else False
        
        self.is_primary_model_primary_stem_only = is_primary_model_primary_stem_only
        self.is_primary_model_secondary_stem_only = is_primary_model_secondary_stem_only

        is_secondary_activated_and_status = self.is_secondary_model_activated and self.model_status
        is_demucs = False
        is_all_stems = True # root.demucs_stems_var.get() == ALL_STEMS
        is_valid_ensemble = not self.is_ensemble_mode and is_all_stems and is_demucs
        is_multi_stem_ensemble_demucs = self.is_multi_stem_ensemble and is_demucs

        if is_secondary_activated_and_status:
            if is_valid_ensemble or self.is_4_stem_ensemble or is_multi_stem_ensemble_demucs:
                for key in DEMUCS_4_SOURCE_LIST:
                    self.secondary_model_data(key)
                    self.secondary_model_4_stem.append(self.secondary_model)
                    self.secondary_model_4_stem_scale.append(self.secondary_model_scale)
                    self.secondary_model_4_stem_names.append(key)
                
                self.demucs_4_stem_added_count = sum(i is not None for i in self.secondary_model_4_stem)
                self.is_secondary_model_activated = any(i is not None for i in self.secondary_model_4_stem)
                self.demucs_4_stem_added_count -= 1 if self.is_secondary_model_activated else 0
                
                if self.is_secondary_model_activated:
                    self.secondary_model_4_stem_model_names_list = [i.model_basename if i is not None else None for i in self.secondary_model_4_stem]
                    self.is_demucs_4_stem_secondaries = True
            else:
                primary_stem = self.ensemble_primary_stem if self.is_ensemble_mode and is_demucs else self.primary_stem
                self.secondary_model_data(primary_stem)

        if self.process_method == DEMUCS_ARCH_TYPE and not is_secondary_model:
            if self.demucs_stem_count >= 3 and self.pre_proc_model_activated:
                self.pre_proc_model = None # root.process_determine_demucs_pre_proc_model(self.primary_stem)
                self.pre_proc_model_activated = True if self.pre_proc_model else False
                self.is_demucs_pre_proc_model_inst_mix = False # root.is_demucs_pre_proc_model_inst_mix_var.get() if self.pre_proc_model else False

        if self.is_vocal_split_model and self.model_status:
            self.is_secondary_model_activated = False
            if self.is_bv_model:
                primary = BV_VOCAL_STEM if self.primary_stem_native == VOCAL_STEM else LEAD_VOCAL_STEM
            else:
                primary = LEAD_VOCAL_STEM if self.primary_stem_native == VOCAL_STEM else BV_VOCAL_STEM
            self.primary_stem, self.secondary_stem = primary, secondary_stem(primary)
            
        self.vocal_splitter_model_data()
            
    def vocal_splitter_model_data(self):
        if not self.is_secondary_model and self.model_status:
            self.vocal_split_model = None # process_determine_vocal_split_model()
            self.is_vocal_split_model_activated = True if self.vocal_split_model else False
            
            if self.vocal_split_model:
                if self.vocal_split_model.bv_model_rebalance:
                    self.is_sec_bv_rebalance = True
            
    def secondary_model_data(self, primary_stem):
        secondary_model_data = None
        self.secondary_model = secondary_model_data[0]
        self.secondary_model_scale = secondary_model_data[1]
        self.is_secondary_model_activated = False if not self.secondary_model else True
        if self.secondary_model:
            self.is_secondary_model_activated = False if self.secondary_model.model_basename == self.model_basename else True
            
        #print("self.is_secondary_model_activated: ", self.is_secondary_model_activated)
              
    def check_if_karaokee_model(self):
        if IS_KARAOKEE in self.model_data.keys():
            self.is_karaoke = self.model_data[IS_KARAOKEE]
        if IS_BV_MODEL in self.model_data.keys():
            self.is_bv_model = self.model_data[IS_BV_MODEL]#
        if IS_BV_MODEL_REBAL in self.model_data.keys() and self.is_bv_model:
            self.bv_model_rebalance = self.model_data[IS_BV_MODEL_REBAL]#
   
    def get_mdx_model_path(self):
        self.model_path = os.path.join(MDX_MODELS_DIR, self.model_name)
        if self.model_name.endswith(CKPT):
            self.is_mdx_ckpt = True     
        self.mixer_path = os.path.join(MDX_MODELS_DIR, f"mixer_val.ckpt")
    
    def get_demucs_model_path(self):
        self.model_path = None

    def get_demucs_model_data(self):

        self.demucs_version = DEMUCS_V4

        for key, value in DEMUCS_VERSION_MAPPER.items():
            if value in self.model_name:
                self.demucs_version = key

        if DEMUCS_UVR_MODEL in self.model_name:
            self.demucs_source_list, self.demucs_source_map, self.demucs_stem_count = DEMUCS_2_SOURCE, DEMUCS_2_SOURCE_MAPPER, 2
        else:
            self.demucs_source_list, self.demucs_source_map, self.demucs_stem_count = DEMUCS_4_SOURCE, DEMUCS_4_SOURCE_MAPPER, 4

        if not self.is_ensemble_mode:
            self.primary_stem = PRIMARY_STEM if self.demucs_stems == ALL_STEMS else self.demucs_stems
            self.secondary_stem = secondary_stem(self.primary_stem)
            
    def get_model_data(self, model_hash_dir, hash_mapper:dict):
        model_settings_json = os.path.join(model_hash_dir, f"{self.model_hash}.json")

        if os.path.isfile(model_settings_json):
            with open(model_settings_json, 'r') as json_file:
                return json.load(json_file)
        else:
            for hash, settings in hash_mapper.items():
                if self.model_hash in hash:
                    return settings

            return self.get_model_data_from_popup()

    def change_model_data(self):
        if self.is_get_hash_dir_only:
            return None
        else:
            return self.get_model_data_from_popup()

    def get_model_data_from_popup(self):
        return None

    def get_model_hash(self):
        self.model_hash = None
        
        if not os.path.isfile(self.model_path):
            self.model_status = False
            self.model_hash is None
        else:
            if model_hash_table:
                for (key, value) in model_hash_table.items():
                    if self.model_path == key:
                        self.model_hash = value
                        break
                    
            if not self.model_hash:
                try:
                    with open(self.model_path, 'rb') as f:
                        f.seek(- 10000 * 1024, 2)
                        self.model_hash = hashlib.md5(f.read()).hexdigest()
                except:
                    self.model_hash = hashlib.md5(open(self.model_path,'rb').read()).hexdigest()
                    
                table_entry = {self.model_path: self.model_hash}
                model_hash_table.update(table_entry)
                
        #print(self.model_name," - ", self.model_hash)

vr_cache_source_mapper = {}
mdx_cache_source_mapper = {}
demucs_cache_source_mapper = {}
def cached_source_callback(process_method, model_name=None):
    model, sources = None, None
    if process_method == VR_ARCH_TYPE:
        mapper = vr_cache_source_mapper
    if process_method == MDX_ARCH_TYPE:
        mapper = mdx_cache_source_mapper
    if process_method == DEMUCS_ARCH_TYPE:
        mapper = demucs_cache_source_mapper
    for key, value in mapper.items():
        if model_name in key:
            model = key
            sources = value
    return model, sources

def uvr_separate(filename : str, export_path = './', count = 1, cpu_only = False):
    # Download the model if it's not downloaded yet
    if not os.path.exists(MDX_MODELS_DIR):
        os.makedirs(MDX_MODELS_DIR)
    hf_hub_download(repo_id="SayanoAI/RVC-Studio", repo_type="dataset", filename="karafan/MDX23C-8KFFT-InstVoc_HQ.ckpt", local_dir=MDX_MODELS_DIR)
    print('Initializing UVR...',end='')
    model = ModelData(model_name='MDX23C_models/MDX23C-8KFFT-InstVoc_HQ.ckpt')
    if cpu_only:
        model.is_gpu_conversion = -1
    audio_file_base = f"{count}_{os.path.splitext(os.path.basename(filename))[0]}"
    set_progress_bar = lambda step, inference_iterations=0 : print('\r{0:07.4f}% '.format(inference_iterations / step * 10), end='')
    write_to_console = lambda progress_text, base_text='':print('{} {}'.format(base_text,progress_text),end='')

    process_data = {
        'model_data': model, 
        'export_path': export_path,
        'audio_file_base': audio_file_base,
        'audio_file': filename,
        'set_progress_bar': set_progress_bar,
        'write_to_console': write_to_console,
        'process_iteration': None,
        'cached_source_callback': cached_source_callback,
        'cached_model_source_holder': None,
        'list_all_models': [],
        'is_ensemble_master': False,
        'is_4_stem_ensemble': False}

    seperator = SeperateMDXC(model, process_data)
    
    seperator.seperate()
    if not cpu_only:
        print('Clearing GPU Cache.')
        clear_gpu_cache()
    # Return output filenames of stems (note that this won't match if you change the model to something with different stem names, like denoise)
    # Also note that save_format is hard-coded to MP3 and mp3_bit_set is hard-coded to 120k
    # Changing model.save_format to WAV does work and the wav_type_set is hard-coded to PCM_32
    audio_file_ext = model.save_format.lower()
    output_vocal_stem = os.path.join(export_path,'{}_(Vocals).{}'.format(audio_file_base,audio_file_ext))
    output_instrumental_stem = os.path.join(export_path, '{}_(Instrumental).{}'.format(audio_file_base,audio_file_ext))
    return output_vocal_stem, output_instrumental_stem

if __name__ == '__main__':
    filenames = sys.argv[1:]
    for idx, filename in enumerate(filenames):
        print(filename)
        vocal_stem, instrumental_stem = uvr_separate(filename, count = idx + 1)
        print(vocal_stem)
        print(instrumental_stem)