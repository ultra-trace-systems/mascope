<script setup>
import { BaseParamField } from '@/lib/base'

const mzCalibrationParams = defineModel('mzCalibrationParams')
</script>

<template>
  <div class="calibration-fields">
    <BaseParamField
      label="Min. match score"
      v-model:param="mzCalibrationParams.match_score_min"
      :range="{ min: 0, max: 1, step: 0.1 }"
      hideSlider
      :small="true"
    />
    <BaseParamField
      label="Refine window [ppm]"
      v-model:param="mzCalibrationParams.refine_window"
      :range="{ min: 0, max: 1000, step: 1 }"
      hideSlider
      :small="true"
    />
    <BaseParamField
      label="Min. isotope abundance"
      v-model:param="mzCalibrationParams.isotope_abundance_min"
      :range="{ min: 0, max: 1, step: 0.05 }"
      hideSlider
      :small="true"
    />
    <BaseParamField
      label="Min. peak intensity"
      v-model:param="mzCalibrationParams.peak_intensity_min"
      :range="{ min: 0, max: 100000, step: 1000 }"
      hideSlider
      :small="true"
    />
    <BaseParamField
      label="Min. signal-to-noise ratio"
      v-model:param="mzCalibrationParams.snr_threshold"
      :range="{ min: 0, max: 1000, step: 10 }"
      hideSlider
      :small="true"
    />
  </div>
</template>


<style scoped>
.calibration-fields {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  column-gap: 1rem;
  row-gap: 0.3rem;
}

.calibration-fields > * {
  min-width: 0;
  max-width: 100%;
  margin-block: 0 !important;
  padding-block: 0 !important;
}

.calibration-fields :deep(fieldset) {
  width: 100%;
  margin: 0.2rem 0;
  gap: 0.5rem;
  justify-content: flex-start;
}

.calibration-fields :deep(.p-floatlabel) {
  width: 100%;
}

.calibration-fields :deep(.p-field),
.calibration-fields :deep(.field),
.calibration-fields :deep(.form-row),
.calibration-fields :deep(.param-field),
.calibration-fields :deep(.p-formgrid) {
  margin-block: 0 !important;
  padding-block: 0 !important;
}

.calibration-fields :deep(label) {
  margin-bottom: 0rem !important;
}

.calibration-fields :deep(.p-inputnumber),
.calibration-fields :deep(.p-inputnumber-input) {
  width: 100%;
}

@media (max-width: 1050px) {
  .calibration-fields {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 520px) {
  .calibration-fields {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>